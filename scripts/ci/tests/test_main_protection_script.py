"""Скрипт защиты `main` описывает защиту целиком и не врёт о результате.

Перенесён из pharmacy-monitor (`tests/test_main_protection_script.py`, после его
PR #87) вместе со скриптом. Там 2026-10-11 нашлось: скрипт слал
`"required_pull_request_reviews": null`, а запись заменяет защиту целиком — null
по описанию API GitHub снимает требование pull request; итоговая печать этого не
показала бы. Здесь лежала копия того же скрипта.

Запустить скрипт против настоящего репозитория для проверки нельзя: это и есть
смена защиты. Поэтому здесь:

- полезная нагрузка достаётся из текста скрипта и сверяется с ожидаемой целиком;
- она же сверяется с ответом GitHub на чтение защиты, записанным 2026-10-11
  (`fixtures/main_protection_live_2026-10-11.json`), — двумя независимыми
  способами: сравнением самого скрипта (jq) и пересчётом на Python здесь;
- скрипт исполняется с подставным `gh` (`fake_gh.py`): видно, какие запросы он
  шлёт, что без `--apply` он не пишет, что пишет только после сверки,
  кончившейся расхождением, или по ответу «ветка не защищена», и что падает,
  когда после записи на GitHub не то.

⚠️ Снимок и скрипт здесь РАСХОДЯТСЯ, и это записано, а не недосмотр: на GitHub
2026-10-11 обязательна одна проверка, `agent-review`, а скрипт её не требует
(решение владельца для всех его репозиториев; подробности — в шапке скрипта).
Всё остальное в снимке совпадает со скриптом. Раздел «Снимок живой защиты»
проверяет ровно это: расходится одна настройка, и скрипт называет её сам.

Чего тест НЕ доказывает: что GitHub примет эту запись и поведёт себя так, как
описано в его API. Подставной `gh` — модель и любую запись принимает. И снимок —
это 2026-10-11: о сегодняшнем состоянии GitHub говорит только `--check` самого
скрипта.

Настоящего `gh` в PATH этих тестов нет: там подставной и каталог со ссылкой на
`jq`. Не исполнится подставной — тест упадёт, а до настоящего не дойдёт. Запись
в тестах идёт только в `test-owner/test-repo`.

Запуск — руками, из корня репозитория (нужны python3, pytest, PyYAML, bash, jq):

    PYTHONDONTWRITEBYTECODE=1 python3 -m pytest scripts/ci/tests -q -p no:cacheprovider

Без этих двух настроек прогон оставит в репозитории `.pytest_cache/` и
`__pycache__/` — в `.gitignore` их может не быть.

⚠️ Ни один workflow этого репозитория тест не запускает: вернуть в скрипт null
или `agent-review` он помешает только тому, кто его запустил.

После записи по слову владельца (`--apply`): новый снимок записывают чтением
(`gh api repos/<owner>/<repo>/branches/main/protection`), и тесты раздела
«Снимок живой защиты» переписывают на «совпадает» — как в образце. Править
снимок руками нельзя: это единственное здесь, что пришло от GitHub. Если меняют
сам набор обязательных проверок — правятся скрипт и `REQUIRED_CHECKS` здесь, в
одном PR.
"""

from __future__ import annotations

import copy
import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import fake_gh  # noqa: E402

REPO = HERE.parents[2]
SCRIPT = REPO / "scripts/ci/configure-main-protection.sh"
WORKFLOWS = REPO / ".github/workflows"
LIVE_2026_10_11 = HERE / "fixtures/main_protection_live_2026-10-11.json"

# --- Единственное, чем этот файл отличается от репозитория к репозиторию -----
OWN_REPO = "rashadoni/Mira"
# Джобы CI, обязательные по скрипту: имя проверки, как его показывает GitHub
# (`name:` джобы, а без него — её ключ). Пустой список — обязательных проверок
# нет: в репозитории нет CI, который шёл бы на каждом pull request.
REQUIRED_CHECKS: list[str] = []
# Workflow, чьи это джобы (файл в .github/workflows). None — когда список пуст.
CI_WORKFLOW: str | None = None
# -----------------------------------------------------------------------------

GITHUB_ACTIONS = 15368
STATUS_CHECKS = (
    {
        "strict": False,
        "checks": [{"context": name, "app_id": GITHUB_ACTIONS} for name in REQUIRED_CHECKS],
    }
    if REQUIRED_CHECKS
    else None
)
# Что стояло на GitHub 2026-10-11 на месте обязательных проверок.
LIVE_STATUS_CHECKS = {"strict": False, "checks": [{"context": "agent-review", "app_id": None}]}
PULL_REQUEST_RULE = {
    "dismiss_stale_reviews": False,
    "require_code_owner_reviews": False,
    "require_last_push_approval": False,
    "required_approving_review_count": 0,
}
EXPECTED = {
    "required_status_checks": STATUS_CHECKS,
    "enforce_admins": True,
    "required_pull_request_reviews": PULL_REQUEST_RULE,
    "restrictions": None,
    "required_linear_history": False,
    "allow_force_pushes": False,
    "allow_deletions": False,
    "block_creations": False,
    "required_conversation_resolution": False,
    "lock_branch": False,
    "allow_fork_syncing": False,
}

OWN_ENDPOINT = f"repos/{OWN_REPO}/branches/main/protection"
TEST_REPO = "test-owner/test-repo"
TEST_ENDPOINT = f"repos/{TEST_REPO}/branches/main/protection"

GET, GET_PUT_GET = ["GET"], ["GET", "PUT", "GET"]


def _on(endpoint: str, methods: list[str]) -> list[tuple[str, str]]:
    return [(method, endpoint) for method in methods]


def _payload() -> dict:
    """JSON, который скрипт шлёт в PUT, — из текста скрипта, без запуска."""
    blocks = re.findall(r"<<'JSON'[^\n]*\n(.*?)\nJSON\n", SCRIPT.read_text(), flags=re.DOTALL)
    assert len(blocks) == 1, "в скрипте должна быть ровно одна полезная нагрузка"
    return json.loads(blocks[0])


def _live() -> dict:
    return json.loads(LIVE_2026_10_11.read_text())


def _as_payload(state: dict) -> dict:
    """Ответ GitHub на чтение защиты — в виде, в котором её задают. Без jq скрипта."""
    checks = state.get("required_status_checks")
    reviews = state.get("required_pull_request_reviews")
    return {
        "required_status_checks": checks
        and {
            "strict": checks["strict"],
            "checks": [{"context": c["context"], "app_id": c["app_id"]} for c in checks["checks"]],
        },
        "enforce_admins": state["enforce_admins"]["enabled"],
        "required_pull_request_reviews": reviews
        and {key: value for key, value in reviews.items() if key != "url"},
        "restrictions": state.get("restrictions"),
        **{name: state[name]["enabled"] for name in fake_gh.SWITCHES},
    }


def _in_sync(endpoint: str = TEST_ENDPOINT) -> dict:
    """Защита, какой её описывает скрипт, — в виде ответа GitHub на чтение."""
    return fake_gh.as_github_returns(copy.deepcopy(EXPECTED), endpoint)


def _drifted(drift) -> dict:
    state = _in_sync()
    drift(state)
    return state


def _without_pull_request_rule(state):
    del state["required_pull_request_reviews"]


def _admins_exempt(state):
    state["enforce_admins"]["enabled"] = False


def _lines(path: Path) -> list:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


class _Run:
    def __init__(self, done: subprocess.CompletedProcess, home: Path):
        self.code = done.returncode
        self.out = done.stdout
        self.err = done.stderr
        self.argv = _lines(home / "argv.jsonl")
        self.calls = _lines(home / "calls.jsonl")
        state = home / "state.json"
        self.state = json.loads(state.read_text()) if state.exists() else None

    @property
    def requests(self) -> list[tuple[str, str]]:
        return [(call["method"], call["endpoint"]) for call in self.calls]

    @property
    def verdict(self) -> str:
        """Последняя строка сверки в stdout."""
        lines = [line for line in self.out.splitlines() if line.startswith("RESULT: ")]
        return lines[-1] if lines else ""

    @property
    def differs(self) -> list[str]:
        return [
            line.split()[1].rstrip(":")
            for line in self.out.splitlines()
            if line.startswith("DIFFERS ")
        ]


@pytest.fixture
def run(tmp_path):
    """Запустить скрипт с подставным `gh`. `state=None` — ветка не защищена."""
    tools = {}
    for tool in ("bash", "jq"):
        tools[tool] = shutil.which(tool)
        if tools[tool] is None:
            pytest.fail(f"тесту нужен {tool}: без него скрипт защиты не исполнить")

    home = tmp_path / "github"
    bin_dir = tmp_path / "bin"
    toolbox = tmp_path / "toolbox"
    empty = tmp_path / "empty"
    for directory in (home, bin_dir, toolbox, empty):
        directory.mkdir()
    (toolbox / "jq").symlink_to(tools["jq"])
    stand_in = bin_dir / "gh"
    stand_in.write_text(
        "#!/bin/sh\n"
        f'exec {shlex.quote(sys.executable)} {shlex.quote(str(HERE / "fake_gh.py"))} "$@"\n'
    )
    stand_in.chmod(0o755)
    path = f"{bin_dir}:{toolbox}"
    assert shutil.which("gh", path=path) == str(stand_in), "в PATH должен быть только подставной gh"

    def _run(
        *args: str,
        state: dict | None,
        put: str = "apply",
        get: str = "ok",
        error: str = "502",
        env: dict[str, str] | None = None,
        with_gh: bool = True,
    ) -> _Run:
        if state is not None:
            (home / "state.json").write_text(json.dumps(state))
        done = subprocess.run(
            [tools["bash"], str(SCRIPT), *args],
            env={
                "PATH": path if with_gh else str(toolbox),
                "HOME": str(empty),
                "GH_CONFIG_DIR": str(empty),
                "FAKE_GH_DIR": str(home),
                "FAKE_GH_PUT": put,
                "FAKE_GH_GET": get,
                "FAKE_GH_GET_ERROR": error,
                **(env or {}),
            },
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        result = _Run(done, home)
        assert len(result.argv) == len(result.calls), (
            f"скрипт позвал gh так, как он им пользоваться не должен: {result.argv}"
        )
        return result

    return _run


# --- Полезная нагрузка: чтением текста скрипта, без запуска ------------------


def test_payload_is_the_agreed_protection():
    assert _payload() == EXPECTED


def test_payload_requires_a_pull_request_and_no_approvals():
    reviews = _payload()["required_pull_request_reviews"]
    assert reviews is not None, (
        "null в required_pull_request_reviews снимает требование pull request: "
        "коммит с зелёными проверками можно будет запушить прямо в main"
    )
    assert reviews["required_approving_review_count"] == 0, (
        "владелец работает один: одобрение, которое некому дать, останавливает доставку"
    )
    assert "bypass_pull_request_allowances" not in reviews


def test_payload_applies_to_admins():
    assert _payload()["enforce_admins"] is True, (
        "на GitHub правила действуют и на администратора; false в записи это снимет"
    )


def _triggers(workflow: dict) -> dict:
    # PyYAML читает ключ `on` как True.
    triggers = workflow.get(True, workflow.get("on"))
    if isinstance(triggers, str):
        return {triggers: None}
    if isinstance(triggers, list):
        return dict.fromkeys(triggers)
    return triggers or {}


def test_agent_review_is_not_a_required_check():
    checks = _payload()["required_status_checks"]
    names = [check["context"] for check in (checks or {}).get("checks", [])]
    assert "agent-review" not in names, (
        "решение владельца для всех его репозиториев: ИИ-ревьюер обязательной проверкой "
        "не бывает — без ключа и при сбое API он зелёный на каждом PR"
    )
    assert "contexts" not in (checks or {}), "старое поле: с ним источник проверки не задаётся"


@pytest.mark.skipif(not REQUIRED_CHECKS, reason="обязательных проверок в этом репозитории нет")
def test_required_checks_are_ci_jobs_that_run_on_every_pull_request():
    checks = _payload()["required_status_checks"]
    names = [check["context"] for check in checks["checks"]]
    assert {check["app_id"] for check in checks["checks"]} == {GITHUB_ACTIONS}

    workflow = yaml.safe_load((WORKFLOWS / CI_WORKFLOW).read_text())
    jobs = {job.get("name", key): job for key, job in workflow["jobs"].items()}
    assert set(names) <= set(jobs), (
        f"обязательной проверки нет среди джоб {CI_WORKFLOW}: она не придёт никогда, "
        "и ни один PR не смержится"
    )
    for name in names:
        assert not {"if", "needs"} & set(jobs[name]), (
            f"джоба «{name}» может быть пропущена, а пропущенную джобу GitHub считает зелёной"
        )
        assert "strategy" not in jobs[name], (
            f"у джобы «{name}» матрица: проверки придут под именами «{name} (…)», а проверка "
            f"«{name}» — никогда, и ни один PR не смержится"
        )
    triggers = _triggers(workflow)
    assert "pull_request" in triggers
    trigger = triggers["pull_request"] or {}
    assert not {"paths", "paths-ignore", "branches-ignore", "types"} & set(trigger), (
        "обязательная проверка должна стартовать на каждом PR в main и на каждом его "
        "коммите: PR мимо фильтра будет ждать её вечно"
    )
    assert "main" in trigger.get("branches", ["main"])


@pytest.mark.skipif(bool(REQUIRED_CHECKS), reason="обязательные проверки в этом репозитории есть")
def test_nothing_is_required_only_while_there_is_no_ci_on_pull_requests():
    """Появился workflow на pull request — его джобы пора вписать в скрипт."""
    assert _payload()["required_status_checks"] is None
    workflows = {path.name: yaml.safe_load(path.read_text()) for path in WORKFLOWS.glob("*.y*ml")}
    on_pull_request = sorted(
        name
        for name, workflow in workflows.items()
        if {"pull_request", "pull_request_target"} & set(_triggers(workflow))
    )
    hint = (
        ", а скрипт защиты не требует ни одной проверки: тесты на мерж не влияют. Впиши "
        "джобы в `checks` скрипта и в REQUIRED_CHECKS здесь (или реши вслух, что не надо)"
    )
    assert on_pull_request in ([], ["agent-review.yml"]), (
        "в репозитории появился workflow, который идёт на pull request" + hint
    )
    for name in on_pull_request:
        jobs = [job.get("name", key) for key, job in workflows[name]["jobs"].items()]
        assert jobs == ["agent-review"], f"в {name} появилась ещё одна джоба" + hint


def test_script_reaches_the_network_only_through_gh_and_writes_once():
    code = [line for line in SCRIPT.read_text().splitlines() if not line.lstrip().startswith("#")]
    assert not [line for line in code if re.search(r"\b(curl|wget|nc)\b", line)]
    writes = [line for line in code if re.search(r"(-X|--method)\b", line)]
    assert len(writes) == 1 and "-X PUT" in writes[0]
    # Диапазоны в шаблоне имени репозитория — ASCII при любой локали вызывающего.
    assert code.index("export LC_ALL=C") < next(
        number for number, line in enumerate(code) if "REPO_SHAPE" in line
    )


# --- Снимок живой защиты от 2026-10-11 ---------------------------------------


def test_recorded_live_protection_differs_from_the_payload_in_required_checks_only():
    live = _as_payload(_live())

    assert live["required_status_checks"] == LIVE_STATUS_CHECKS
    assert {key for key in EXPECTED if EXPECTED[key] != live[key]} == {"required_status_checks"}
    assert set(live) == set(EXPECTED)


def test_stand_in_answers_a_write_the_way_github_answered_a_read():
    assert fake_gh.as_github_returns(_as_payload(_live()), OWN_ENDPOINT) == _live()


@pytest.mark.parametrize("args", [["--check"], []], ids=["--check", "no flag"])
def test_script_names_the_one_difference_from_the_recorded_live_protection(run, args):
    result = run(*args, state=_live())

    assert result.code == 1, result.out + result.err
    assert result.verdict == "RESULT: MISMATCH"
    assert result.differs == ["required_status_checks"]
    for line in (
        "pull request required  : true",
        "required approvals     : 0",
        "required checks        : agent-review",
        "applies to admins      : true",
        "force pushes allowed   : false",
        "branch deletion allowed: false",
    ):
        assert line in result.out.splitlines()
    assert "Nothing was sent." in result.out
    assert result.requests == _on(OWN_ENDPOINT, GET)


def test_apply_would_replace_the_recorded_live_protection_with_the_payload(run):
    """Что сделает `--apply` по слову владельца — на снимке, в `test-owner/test-repo`."""
    live = fake_gh.as_github_returns(_as_payload(_live()), TEST_ENDPOINT)

    result = run("--apply", TEST_REPO, state=live)

    assert result.code == 0, result.out + result.err
    assert result.requests == _on(TEST_ENDPOINT, GET_PUT_GET)
    assert json.loads(result.calls[1]["body"]) == EXPECTED
    assert result.verdict == "RESULT: match"
    assert result.state == _in_sync()


# --- Без --apply: только чтение ----------------------------------------------


@pytest.mark.parametrize("args", [["--check"], [], [TEST_REPO]], ids=["--check", "no flag", "repo"])
def test_nothing_is_written_without_apply_whatever_github_has(run, args):
    result = run(*args, state=_drifted(_without_pull_request_rule))

    assert result.code == 1
    assert "Nothing was sent." in result.out
    assert [method for method, _ in result.requests] == GET


@pytest.mark.skipif(len(REQUIRED_CHECKS) < 2, reason="переставлять нечего")
def test_order_of_checks_does_not_matter(run):
    def reorder(state):
        state["required_status_checks"]["checks"].reverse()
        state["required_status_checks"]["contexts"].reverse()

    result = run("--check", TEST_REPO, state=_drifted(reorder))

    assert result.code == 0, result.out + result.err
    assert result.verdict == "RESULT: match"


def _drift_cases() -> list:
    cases = []

    def case(name, setting, drift, line=None):
        cases.append(pytest.param(drift, setting, line, id=name))

    def checks(state):
        return state["required_status_checks"]

    def reviews(state):
        return state["required_pull_request_reviews"]

    shown = {
        "allow_force_pushes": "force pushes allowed   : {}",
        "allow_deletions": "branch deletion allowed: {}",
    }
    for switch in fake_gh.SWITCHES:
        line = shown.get(switch)
        case(
            f"{switch}_on",
            switch,
            lambda s, k=switch: s[k].update(enabled=True),
            line and line.format("true"),
        )
        case(
            f"{switch}_not_returned",
            switch,
            lambda s, k=switch: s.pop(k),
            line and line.format("null"),
        )

    case("admins_exempt", "enforce_admins", _admins_exempt, "applies to admins      : false")
    case(
        "admins_not_returned",
        "enforce_admins",
        lambda s: s.pop("enforce_admins"),
        "applies to admins      : null",
    )

    rule = "required_pull_request_reviews"
    case("no_pull_request_rule", rule, _without_pull_request_rule, "pull request required  : false")
    case(
        "one_approval",
        rule,
        lambda s: reviews(s).update(required_approving_review_count=1),
        "required approvals     : 1",
    )
    case(
        "approvals_not_returned",
        rule,
        lambda s: reviews(s).pop("required_approving_review_count"),
        "required approvals     : null",
    )
    for flag in (
        "dismiss_stale_reviews",
        "require_code_owner_reviews",
        "require_last_push_approval",
    ):
        case(f"{flag}_on", rule, lambda s, k=flag: reviews(s).update({k: True}))
    allowed = {"users": [{"login": "someone"}], "teams": [], "apps": []}
    case(
        "bypass_allowed", rule, lambda s: reviews(s).update(bypass_pull_request_allowances=allowed)
    )
    case(
        "bypass_object_empty", rule, lambda s: reviews(s).update(bypass_pull_request_allowances={})
    )
    case("dismissal_restricted", rule, lambda s: reviews(s).update(dismissal_restrictions=allowed))

    status = "required_status_checks"

    def agent_review_back(state):
        if status not in state:
            url = state["url"]
            state[status] = {
                "url": f"{url}/required_status_checks",
                "strict": False,
                "contexts": [],
                "contexts_url": f"{url}/required_status_checks/contexts",
                "checks": [],
            }
        checks(state)["contexts"].append("agent-review")
        checks(state)["checks"].append({"context": "agent-review", "app_id": None})

    def shown_checks(names):
        return "required checks        : " + ", ".join(sorted(names))

    case(
        "agent_review_back",
        status,
        agent_review_back,
        shown_checks([*REQUIRED_CHECKS, "agent-review"]),
    )
    if REQUIRED_CHECKS:
        gone = REQUIRED_CHECKS[-1]

        def one_check_gone(state):
            checks(state)["contexts"].remove(gone)
            checks(state)["checks"] = [
                check for check in checks(state)["checks"] if check["context"] != gone
            ]

        def check_listed_twice(state):
            checks(state)["checks"].append(dict(checks(state)["checks"][0]))

        def check_named_like_a_verdict(state):
            checks(state)["checks"].append(
                {"context": "x\nRESULT: match", "app_id": GITHUB_ACTIONS}
            )

        case("no_status_checks", status, lambda s: s.pop(status), shown_checks([]))
        case("one_check_gone", status, one_check_gone, shown_checks(REQUIRED_CHECKS[:-1]))
        case("check_listed_twice", status, check_listed_twice)
        case("check_named_like_a_verdict", status, check_named_like_a_verdict)
        case("check_from_any_source", status, lambda s: checks(s)["checks"][-1].update(app_id=None))
        case("check_source_not_returned", status, lambda s: checks(s)["checks"][-1].pop("app_id"))
        case("checks_not_returned", status, lambda s: checks(s).pop("checks"))
        case("strict", status, lambda s: checks(s).update(strict=True))
        case("strict_not_returned", status, lambda s: checks(s).pop("strict"))
    else:
        # Обязательных проверок скрипт не ставит: любая, что есть на GitHub, — расхождение.
        def empty_list_of_checks(state):
            agent_review_back(state)
            checks(state).update(contexts=[], checks=[])

        def verdict_as_a_check(state):
            agent_review_back(state)
            checks(state)["checks"][0]["context"] = "x\nRESULT: match"

        case("empty_list_of_checks", status, empty_list_of_checks, shown_checks([]))
        case("check_named_like_a_verdict", status, verdict_as_a_check)

    case("push_restricted", "restrictions", lambda s: s.update(restrictions=allowed))
    empty = {"users": [], "teams": [], "apps": []}
    case("push_restricted_to_nobody", "restrictions", lambda s: s.update(restrictions=empty))
    return cases


@pytest.mark.parametrize(("drift", "setting", "line"), _drift_cases())
def test_check_names_the_setting_that_drifted_and_sends_nothing(run, drift, setting, line):
    result = run("--check", TEST_REPO, state=_drifted(drift))

    assert result.code == 1, result.out + result.err
    assert result.out.splitlines()[-2] == "RESULT: MISMATCH"
    assert result.differs == [setting]
    if line is not None:
        assert line in result.out.splitlines()
    assert result.requests == _on(TEST_ENDPOINT, GET)


def test_check_says_which_side_has_what(run):
    result = run("--check", TEST_REPO, state=_drifted(_without_pull_request_rule))

    line = next(line for line in result.out.splitlines() if line.startswith("DIFFERS "))
    script_side, github_side = line.removeprefix(
        "DIFFERS required_pull_request_reviews: this script sets "
    ).split(", GitHub has ")
    assert json.loads(script_side) == PULL_REQUEST_RULE
    assert github_side == "null"


def test_check_reports_an_unprotected_branch(run):
    result = run("--check", TEST_REPO, state=None)

    assert result.code == 1
    assert "no branch protection at all" in result.out
    assert result.verdict == ""
    assert result.requests == _on(TEST_ENDPOINT, GET)


# Чтение не удалось или кончилось без вердикта: это не «не защищена» и не «расходится».
UNREADABLE = sorted(fake_gh.GET_ERRORS)


@pytest.mark.parametrize("error", UNREADABLE)
@pytest.mark.parametrize("flag", ["--check", "--apply"])
@pytest.mark.parametrize("drift", [lambda state: None, _admins_exempt], ids=["in sync", "drifted"])
def test_nothing_is_written_over_protection_that_could_not_be_read(run, flag, error, drift):
    state = _drifted(drift)

    result = run(flag, TEST_REPO, state=state, get="fail_first", error=error)

    assert result.code == 3, result.out + result.err
    assert "Nothing was sent." in result.err
    assert "no branch protection" not in result.out
    assert result.requests == _on(TEST_ENDPOINT, GET)
    assert result.state == state


def test_gh_is_required(run):
    result = run("--apply", TEST_REPO, state=_drifted(_admins_exempt), with_gh=False)

    assert result.code == 3
    assert "gh is required" in result.err
    assert result.argv == []


# --- Запись: только --apply --------------------------------------------------


def test_apply_sends_nothing_when_github_already_matches(run):
    result = run("--apply", TEST_REPO, state=_in_sync())

    assert result.code == 0, result.out + result.err
    assert "nothing sent" in result.out
    assert result.requests == _on(TEST_ENDPOINT, GET)


def test_apply_writes_the_payload_when_pull_request_rule_is_missing(run):
    result = run("--apply", TEST_REPO, state=_drifted(_without_pull_request_rule))

    assert result.code == 0, result.out + result.err
    assert result.requests == _on(TEST_ENDPOINT, GET_PUT_GET)
    assert json.loads(result.calls[1]["body"]) == EXPECTED
    assert result.verdict == "RESULT: match"
    assert result.state == _in_sync()


def test_apply_protects_an_unprotected_branch(run):
    result = run("--apply", TEST_REPO, state=None)

    assert result.code == 0, result.out + result.err
    assert result.requests == _on(TEST_ENDPOINT, GET_PUT_GET)
    assert json.loads(result.calls[1]["body"]) == EXPECTED
    assert result.verdict == "RESULT: match"


@pytest.mark.parametrize(
    ("put", "setting", "line"),
    [
        (
            "drop_pull_request_rule",
            "required_pull_request_reviews",
            "pull request required  : false",
        ),
        ("keep_admins_exempt", "enforce_admins", "applies to admins      : false"),
        ("keep_agent_review", "required_status_checks", "required checks        : agent-review"),
        pytest.param(
            "unpin_checks",
            "required_status_checks",
            None,
            marks=pytest.mark.skipif(not REQUIRED_CHECKS, reason="обязательных проверок нет"),
        ),
    ],
)
def test_apply_fails_when_github_ends_up_in_another_state(run, put, setting, line):
    """Прежняя печать сказала бы здесь `pull_request_only: true` и вышла с нулём."""
    result = run("--apply", TEST_REPO, state=None, put=put)

    assert result.requests == _on(TEST_ENDPOINT, GET_PUT_GET)
    assert result.code == 1
    assert result.verdict == "RESULT: MISMATCH"
    assert result.differs == [setting]
    if line is not None:
        assert line in result.out.splitlines()
    assert "NOT in the state" in result.err


def test_apply_fails_when_the_branch_is_unprotected_after_an_accepted_write(run):
    result = run("--apply", TEST_REPO, state=None, put="accept_and_drop")

    assert result.requests == _on(TEST_ENDPOINT, GET_PUT_GET)
    assert result.code == 1
    assert result.out.count("no branch protection at all") == 2  # до записи и после
    assert "NOT in the state" in result.err


def test_apply_fails_when_github_refuses_the_write(run):
    state = _drifted(_without_pull_request_rule)

    result = run("--apply", TEST_REPO, state=state, put="reject")

    assert result.code == 3
    assert "Sent." not in result.out
    assert "ended in an error" in result.err
    # Что стоит на GitHub после несостоявшейся записи, скрипт читает, а не утверждает.
    assert result.requests == _on(TEST_ENDPOINT, GET_PUT_GET)
    assert "RESULT: MISMATCH" in result.err
    assert result.state == state


def test_apply_says_what_is_set_after_a_refused_write_to_an_unprotected_branch(run):
    result = run("--apply", TEST_REPO, state=None, put="reject")

    assert result.code == 3
    assert result.requests == _on(TEST_ENDPOINT, GET_PUT_GET)
    assert "no branch protection at all" in result.err
    assert "Could not read" not in result.err


def test_apply_does_not_claim_a_refusal_when_the_write_went_through(run):
    result = run(
        "--apply", TEST_REPO, state=_drifted(_without_pull_request_rule), put="error_after_apply"
    )

    assert result.code == 3
    assert "Nothing" not in result.err and "did not" not in result.err
    assert result.requests == _on(TEST_ENDPOINT, GET_PUT_GET)
    assert "RESULT: match" in result.err


def test_apply_admits_it_when_neither_the_write_nor_the_reading_after_it_worked(run):
    result = run(
        "--apply",
        TEST_REPO,
        state=_drifted(_without_pull_request_rule),
        put="reject",
        get="fail_after_put",
    )

    assert result.code == 3
    assert result.requests == _on(TEST_ENDPOINT, GET_PUT_GET)
    assert "Could not read" in result.err


@pytest.mark.parametrize("error", UNREADABLE)
def test_apply_fails_when_the_result_cannot_be_read_back(run, error):
    result = run(
        "--apply",
        TEST_REPO,
        state=_drifted(_without_pull_request_rule),
        get="fail_after_put",
        error=error,
    )

    assert result.requests == _on(TEST_ENDPOINT, GET_PUT_GET)
    assert result.code == 3
    assert "could not be read back" in result.err
    assert result.verdict == "RESULT: MISMATCH"  # сверка до записи; после неё сверки нет


FOREIGN = {
    "BRANCH": "release",
    "REPO": "other-owner/other-repo",
    "ENDPOINT": "repos/other-owner/other-repo/branches/release/protection",
    "MODE": "apply",
    "DESIRED": "{}",
    "COMPARE": "| empty",
    "GH": "false",
}


@pytest.mark.parametrize("flag", ["--check", "--apply"])
def test_nothing_about_the_target_comes_from_the_environment(run, flag):
    result = run(flag, TEST_REPO, state=_drifted(_without_pull_request_rule), env=FOREIGN)

    expected = GET_PUT_GET if flag == "--apply" else GET
    assert result.requests == _on(TEST_ENDPOINT, expected)
    if flag == "--apply":
        assert json.loads(result.calls[1]["body"]) == EXPECTED


def test_default_repository_and_mode_do_not_come_from_the_environment(run):
    result = run(state=_drifted(_without_pull_request_rule), env=FOREIGN)

    assert result.code == 1
    assert result.requests == _on(OWN_ENDPOINT, GET)


@pytest.mark.parametrize(
    "args",
    [
        ["--chek"],
        ["--checks"],
        ["--apply-now"],
        ["-h"],
        ["check"],
        ["apply"],
        ["—check"],
        [" --apply"],
        [""],
        ["owner"],
        ["/repo"],
        ["owner/"],
        ["owner/."],
        ["owner/.."],
        ["owner/re po"],
        ["{owner}/repo"],
        ["owner/repo/extra"],
        ["--apply", ""],
        ["--apply", "apply"],
        ["--apply", "--check"],
        ["--check", "--apply"],
        ["--apply", "--apply"],
        [TEST_REPO, "--apply"],
        [TEST_REPO, "--check"],
        ["--apply", TEST_REPO, "extra"],
    ],
    ids=lambda args: repr(" ".join(args)),
)
def test_unrecognised_arguments_stop_before_the_first_request(run, args):
    result = run(*args, state=_drifted(_without_pull_request_rule))

    assert result.code == 2
    assert result.argv == []
