#!/usr/bin/env bash
# Layer 1 of docs/DELIVERY-ARCHITECTURE.md, as a script rather than a promise.
#
# What it describes for `main`:
#   - changes arrive only through a pull request: `required_pull_request_reviews`
#     is set, with zero required approvals — the owner works alone, and a rule
#     nobody can satisfy stops delivery instead of protecting it
#   - no required status check: this repository has no CI job that runs on
#     every pull request, so there is nothing true to require (see the
#     payload)
#   - the rules apply to administrators too (`enforce_admins`): nobody merges a
#     red pull request. That is what GitHub has for this branch today, and what
#     the owner decided for pharmacy-monitor on 2026-10-11; kept here
#   - no force push, no branch deletion
# That these settings behave as named — a direct push is refused, a status of
# the same name from another source does not count — is GitHub's description
# of them. Nobody here tried.
#
# THIS IS NOT WHAT GITHUB HAS TODAY, and that is known (read 2026-10-11). The
# pull-request requirement, `enforce_admins` and the two "no" rules are on
# GitHub as written here. The required check is not: GitHub has the single
# context `agent-review`. So `--check` ends in "RESULT: MISMATCH" with exit
# code 1, naming `required_status_checks`, until the owner says to write. The
# test keeps that reading (scripts/ci/tests/fixtures/).
#
# Why `agent-review` is not in the payload. The owner's decision for all his
# repositories (2026-09-11): the gate before `main` is the CI that GitHub
# itself runs; no paid AI reviewer in CI, and never a required one.
# agent-review.yml is red by design in two cases: a blocking finding of its
# model, and a diff over 400 000 bytes (refused whenever a key is set, before
# the API is called). When its API key is missing, or the API answers with an
# error status, it is green — on purpose, so that a provider outage cannot
# stop delivery. A key is set in this repository's secrets; whether it works
# is not known.
# Before the pull request with this change (2026-10-11) the workflow had never
# run in this repository. In leaddrive-budgeting pull request #4 (2026-09-10)
# the same workflow was green under its own comment that the API had answered
# 400 and nobody had read the change.
# While it is the only required check, a pull request can be merged with
# nothing having tested the code and nobody having read it.
#
# Usage:
#   bash scripts/ci/configure-main-protection.sh --check [owner/repo]
#       read-only: compares the live protection with the payload below and
#       sends nothing.
#   bash scripts/ci/configure-main-protection.sh --apply [owner/repo]
#       writes the payload — only after a comparison that ended in "MISMATCH",
#       or on a branch GitHub reports as unprotected. Then reads the result
#       back.
# Exit codes, both modes: 0 = GitHub has what is written here (and nothing was
# sent, or the write was read back and equals the payload); 1 = it has
# something else; 2 = arguments not understood, nothing requested; 3 = could
# not tell — a read or the write failed. A status of 1 with neither a
# "RESULT: MISMATCH" line nor the "no branch protection" line above it is the
# script itself failing, not a finding.
# Without a flag it is `--check`. Always type `--check` anyway: the copies of
# this file from before this change (see below) do not know flags, and every
# run of one is a write attempt.
#
# Changed 2026-10-11, after the same file was fixed in pharmacy-monitor (its
# pull request #87), from which this one is copied. Before this change:
#   - the payload said `"required_pull_request_reviews": null`, while the
#     header promised "only through a pull request". GitHub's API description
#     reads null as "disable", and the PUT replaces the whole protection
#     object: a run against this repository would have removed the
#     pull-request requirement that is set on GitHub, and a commit could then
#     have been pushed straight to `main`. (Read from the API description.
#     Never tried. The requirement that is on GitHub was not put there by this
#     script.)
#   - it sent `"enforce_admins": false`, where GitHub has it switched on
#   - the closing report printed `pull_request_only` from the status-check
#     field, so it said `true` whatever the pull-request field held
#   - it wrote on every run, with no comparison first. Run with no arguments
#     it aimed at this repository's name under the previous account
#     (`rashadrahimov/…`), not at this repository; given `rashadoni/…` as its
#     argument it would have written here
# Now the payload carries the pull-request requirement, the report is a
# comparison of what GitHub returns with the payload, and writing takes an
# explicit flag.
#
# NOT VERIFIED BY A RUN, as of 2026-10-11: `--apply` has never sent its write
# to GitHub — branch protection is not changed for the sake of a test. What was
# checked instead: the payload against the live protection, by reading it (it
# differs in the required check only, as said above); the comparison filter in
# gh's own jq, on that same read; and the script against a stand-in `gh` that
# never reaches the network (scripts/ci/tests/test_main_protection_script.py —
# run by hand, see its first lines; no workflow of this repository runs it).
# After the first real write, rewrite this paragraph and the "NOT WHAT GITHUB
# HAS TODAY" one, and in the same pull request everything that still names
# `agent-review` as the required check: docs/DELIVERY-ARCHITECTURE.md (the
# note in Layer 1, Layers 1 and 2, rule 3 at the end), the delivery section
# at the top of AGENTS.md / CLAUDE.md, and the comment at the top of
# .github/workflows/agent-review.yml ("the ONLY required check on main").
#
# Before `--apply`:
#   - it is the owner's call for this repository, not an agent's
#   - know what it does here: it removes `agent-review` from the required
#     checks and puts nothing in its place. After that the gate is the
#     pull-request requirement alone. If the key of agent-review works, that is
#     a loss: a pull request it would have stopped on a blocking finding
#     becomes mergeable. If the key does not work, the one thing lost is its
#     refusal of a diff over 400 000 bytes: on anything smaller the check is
#     green whatever the change
#   - use the file from `origin/main` after a `git fetch`, not a local `main`
#     and not another branch. Every branch and worktree cut before this change
#     was merged carries the old file, whatever its date. The old file is the
#     one without `--apply` in it:
#       grep -c -- '--apply' scripts/ci/configure-main-protection.sh   # 0 = old
#   - run `--check` first and read what differs: the write replaces every
#     setting below, including one somebody changed on purpose — a required
#     check added by hand is removed by it
set -euo pipefail
# Character ranges in the patterns below mean ASCII, whatever the caller's locale.
export LC_ALL=C

usage() {
  echo "Usage: bash scripts/ci/configure-main-protection.sh --check|--apply [owner/repo]" >&2
  exit 2
}

# Writing is never the default, and an argument that is not exactly a flag or
# an owner/repo pair stops the script before the first request.
MODE=check
case "${1:-}" in
  --check) shift ;;
  --apply) MODE=apply; shift ;;
esac
[[ $# -le 1 ]] || usage

# This repository under the rashadoni account. The old default named it under
# the previous account, `rashadrahimov/…`.
REPO="rashadoni/Mira"
[[ $# -eq 0 ]] || REPO="$1"
REPO_SHAPE='^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+$'
[[ "${REPO}" =~ ${REPO_SHAPE} ]] || usage
[[ "${REPO##*/}" != "." && "${REPO##*/}" != ".." ]] || usage

# Not taken from the environment: a BRANCH exported for something else must not
# point a protection write at another branch.
BRANCH="main"
ENDPOINT="repos/${REPO}/branches/${BRANCH}/protection"

command -v gh >/dev/null || { echo "gh is required" >&2; exit 3; }

# The whole protection object. PUT replaces it. The API description gives every
# optional setting a default, so one left out is expected to fall back to that
# default rather than keep its value (inferred from the description, not stated
# there, not tried) — which is why every top-level setting the endpoint takes
# is spelled out, and why `required_pull_request_reviews` must never be null
# again. Inside it, `dismissal_restrictions` and
# `bypass_pull_request_allowances` are left out on purpose: nobody is to bypass
# the pull-request requirement. That leaving them out clears an allowance
# already set is assumed too; the comparison shows one that survived.
#
# `required_status_checks` is null: no required check. The only workflow of
# this repository is agent-review.yml, and it is not a gate (see the
# header). When a CI workflow appears that runs on EVERY pull request to
# main — no path filters, no `if:` or `needs:` on the job — list its jobs
# here and in REQUIRED_CHECKS of the test, in this shape (15368 is GitHub
# Actions):
#   "required_status_checks": {
#     "strict": false,
#     "checks": [{"context": "<job name>", "app_id": 15368}]
#   },
# A path-filtered required check is unmergeable for ever. That null removes
# the check set on GitHub is the API description; not tried.
IFS= read -r -d '' DESIRED <<'JSON' || true
{
  "required_status_checks": null,
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": false,
    "require_code_owner_reviews": false,
    "require_last_push_approval": false,
    "required_approving_review_count": 0
  },
  "restrictions": null,
  "required_linear_history": false,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "block_creations": false,
  "required_conversation_resolution": false,
  "lock_branch": false,
  "allow_fork_syncing": false
}
JSON

# Turns GitHub's answer into the shape of the payload and compares the two,
# setting by setting. It runs inside `gh api --jq`, with the payload bound to
# $want (JSON is valid jq). The last line it prints is the verdict; everything
# before it is for the reader, and is taken from GitHub's answer, not from the
# payload. Only the two exact verdict lines count: output that ends in anything
# else is "could not tell", never "different".
#
# What "match" means: every setting this script writes has, on GitHub, the
# value written here. A setting GitHub does not return counts as a difference,
# not as "off" — except `required_status_checks`: GitHub leaves it out when
# the setting is off, so there absent and null are the same thing. (The
# setting switched on with an empty list of checks is not the same thing, and
# counts as a difference from null.) What it does not look at: anything this
# endpoint does not write — commit signatures, repository rulesets.
IFS= read -r -d '' COMPARE <<'JQ' || true
| ($want
   | if .required_status_checks == null then .
     else .required_status_checks.checks |= sort_by(.context)
     end) as $want
| {
    required_status_checks: (
      .required_status_checks
      | if . == null then null
        else {strict, checks: ((.checks // []) | map({context, app_id}) | sort_by(.context))}
        end
    ),
    enforce_admins: .enforce_admins.enabled,
    required_pull_request_reviews: (
      .required_pull_request_reviews | if . == null then null else del(.url) end
    ),
    restrictions: .restrictions,
    required_linear_history: .required_linear_history.enabled,
    allow_force_pushes: .allow_force_pushes.enabled,
    allow_deletions: .allow_deletions.enabled,
    block_creations: .block_creations.enabled,
    required_conversation_resolution: .required_conversation_resolution.enabled,
    lock_branch: .lock_branch.enabled,
    allow_fork_syncing: .allow_fork_syncing.enabled
  } as $live
| [$want | keys[] | select($want[.] != $live[.])] as $differs
| "pull request required  : \($live.required_pull_request_reviews != null)",
  "required approvals     : \($live.required_pull_request_reviews.required_approving_review_count)",
  "required checks        : \([$live.required_status_checks.checks // [] | .[] | .context] | join(", "))",
  "applies to admins      : \($live.enforce_admins)",
  "force pushes allowed   : \($live.allow_force_pushes)",
  "branch deletion allowed: \($live.allow_deletions)",
  ($differs[] | "DIFFERS \(.): this script sets \($want[.] | tojson), GitHub has \($live[.] | tojson)"),
  (if ($differs | length) == 0 then "RESULT: match" else "RESULT: MISMATCH" end)
JQ

NL=$'\n'

# Reads the protection and sets STATE (what to show) and VERDICT:
#   match | mismatch   the comparison ran and said so
#   unprotected        GitHub answered that the branch has no protection
#   unknown            anything else: an error, a refusal, an answer this
#                      script cannot read. Never a reason to write.
# "Unprotected" is GitHub's 404 body, which gh prints on stdout: a JSON object
# carrying that message. The comparison's own output never starts with a brace.
compare() {
  VERDICT=unknown
  if STATE="$(gh api "${ENDPOINT}" -H "Accept: application/vnd.github+json" \
    --jq "(${DESIRED}) as \$want ${COMPARE}")"; then
    case "${STATE##*"${NL}"}" in
      "RESULT: match") VERDICT=match ;;
      "RESULT: MISMATCH") VERDICT=mismatch ;;
    esac
  elif [[ "${STATE}" == "{"* && "${STATE}" == *'"Branch not protected"'* ]]; then
    VERDICT=unprotected
  fi
}

show() {
  case "${VERDICT}" in
    match | mismatch) printf '%s\n' "${STATE}" ;;
    unprotected) echo "${REPO}@${BRANCH} has no branch protection at all." ;;
    *)
      [[ -z "${STATE}" ]] || printf '%s\n' "${STATE}" >&2
      echo "Could not read the protection of ${REPO}@${BRANCH}: see above." >&2
      ;;
  esac
}

echo "Comparing ${REPO}@${BRANCH} with this script."
compare
show
case "${VERDICT}" in
  match)
    echo "Nothing to change, nothing sent."
    exit 0
    ;;
  mismatch | unprotected) ;;
  *)
    # Writing over a state nobody has seen is exactly what this script is for
    # preventing.
    echo "Nothing was sent." >&2
    exit 3
    ;;
esac

if [[ "${MODE}" != apply ]]; then
  echo "Nothing was sent. Writing is --apply, and it is the owner's call."
  exit 1
fi

echo
echo "Writing branch protection to ${REPO}@${BRANCH}"
if ! response="$(gh api -X PUT "${ENDPOINT}" -H "Accept: application/vnd.github+json" \
  --input - <<<"${DESIRED}")"; then
  printf '%s\n' "${response}" >&2
  echo "The write request ended in an error: see above. What GitHub has now:" >&2
  compare
  show >&2
  exit 3
fi

echo "Sent. Read back from GitHub:"
compare
show
case "${VERDICT}" in
  match) ;;
  mismatch | unprotected)
    echo >&2
    echo "GitHub accepted the write, but ${REPO}@${BRANCH} is NOT in the state this" >&2
    echo "script describes: see above. Do not report it as protected." >&2
    exit 1
    ;;
  *)
    echo "The write was accepted, but the protection could not be read back." >&2
    echo "Treat ${REPO}@${BRANCH} as unverified until --check passes." >&2
    exit 3
    ;;
esac

echo
echo "Note: the rules apply to administrators too. No status check is required"
echo "on this branch: the gate is the pull-request requirement alone, until a CI"
echo "job that runs on every pull request is listed in this script."
