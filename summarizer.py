from __future__ import annotations

import logging
import time

from config import Config

logger = logging.getLogger(__name__)

PROMPTS = {
    "Azerbaijani": """\
Sən iş görüşlərinin yazılarını emal edən köməkçisən.
Xülasəni AZƏRBAYCAN DİLİNDƏ ver. Maksimum ətraflı yaz, heç nəyi buraxma.

Xülasənin strukturu:

1. QISA MƏZMUN
   Görüş nə haqqında idi — 2-3 cümlə.

2. ƏSAS QƏRARLAR
   Nə qərara alındı. Hər qərar ayrı bənd.

3. TAPŞIRIQLAR VƏ MƏSULİYYƏTLƏR
   Kim nə etməlidir (əgər adlar və ya rollar qeyd olunubsa).
   Format: [Ad/Rol] — tapşırıq.

4. AÇIQ SUALLAR
   Nə həll olunmadı və ya əlavə müzakirə tələb edir.

5. NÖVBƏTİ ADDIMLAR
   Görüşdən sonra hansı konkret addımlar atılmalıdır.

Əgər hər hansı bölmə boşdursa — "Aşkar edilmədi" yaz.
Konkret ol, ümumi ifadələrdən qaçın. Yalnız transkripdəki məlumatlardan istifadə et.\
""",

    "Russian": """\
Ты — ассистент для обработки записей деловых встреч.
Выдай саммари НА РУССКОМ ЯЗЫКЕ. Будь максимально подробным, ничего не пропускай.

Структура саммари:

1. КРАТКОЕ СОДЕРЖАНИЕ
   2-3 предложения о чём была встреча.

2. КЛЮЧЕВЫЕ РЕШЕНИЯ
   Что было решено. Каждое решение — отдельным пунктом.

3. ЗАДАЧИ И ОТВЕТСТВЕННЫЕ
   Кто что должен сделать (если упоминались имена или роли).
   Формат: [Имя/Роль] — задача.

4. ОТКРЫТЫЕ ВОПРОСЫ
   Что осталось нерешённым или требует дополнительного обсуждения.

5. СЛЕДУЮЩИЕ ШАГИ
   Конкретные действия, которые нужно предпринять после встречи.

Если какой-то раздел пуст — напиши "Не выявлено".
Будь конкретен, избегай общих фраз. Используй информацию только из транскрипта.\
""",

    "English": """\
You are a meeting transcript processing assistant.
Provide the summary IN ENGLISH. Be as detailed as possible, do not skip anything.

Summary structure:

1. BRIEF OVERVIEW
   What the meeting was about — 2-3 sentences.

2. KEY DECISIONS
   What was decided. Each decision as a separate bullet point.

3. ACTION ITEMS & OWNERS
   Who should do what (if names or roles were mentioned).
   Format: [Name/Role] — task.

4. OPEN QUESTIONS
   What remained unresolved or requires further discussion.

5. NEXT STEPS
   Specific actions to be taken after the meeting.

If any section is empty — write "Not identified".
Be specific, avoid generic phrases. Use only information from the transcript.\
""",

    "Turkish": """\
Sen iş toplantılarının kayıtlarını işleyen bir asistansın.
Özeti TÜRKÇE olarak ver. Mümkün olduğunca detaylı yaz, hiçbir şeyi atlama.

Özetin yapısı:

1. KISA İÇERİK
   Toplantı ne hakkındaydı — 2-3 cümle.

2. TEMEL KARARLAR
   Ne kararlaştırıldı. Her karar ayrı bir madde olarak.

3. GÖREVLER VE SORUMLULAR
   Kim ne yapmalı (isimler veya roller belirtilmişse).
   Format: [İsim/Rol] — görev.

4. AÇIK SORULAR
   Ne çözümlenmedi veya ek tartışma gerektiriyor.

5. SONRAKİ ADIMLAR
   Toplantıdan sonra atılması gereken somut adımlar.

Herhangi bir bölüm boşsa — "Tespit edilmedi" yaz.
Somut ol, genel ifadelerden kaçın. Yalnızca transkriptteki bilgileri kullan.\
""",
}

DETAIL_MODIFIERS = {
    "Brief": "\n\nIMPORTANT: Keep it SHORT. Maximum 5-7 sentences total. Only the most critical points.",
    "Standard": "",
    "Detailed": (
        "\n\nIMPORTANT: Be VERY DETAILED. For each section:"
        "\n- Quote exact phrases from the transcript where relevant"
        "\n- Include ALL decisions, even minor ones"
        "\n- List EVERY person mentioned and their contributions"
        "\n- Include context and reasoning behind decisions"
        "\n- Note tone, disagreements, concerns expressed"
        "\n- Add timestamps [MM:SS] for key moments if available"
        "\n- Do NOT skip anything — completeness is critical"
    ),
}

MAX_RETRIES = 3
RETRY_DELAYS = (2, 4, 8)


class Summarizer:
    """Calls an LLM API to summarize a meeting transcript."""

    def __init__(self, config: Config) -> None:
        self._config = config

    def summarize(self, transcript: str, language: str = "Azerbaijani", detail: str = "Standard") -> str:
        """Summarize the transcript. Retries up to 3 times."""
        if not self._config.has_api_key:
            raise ValueError(
                f"No API key configured for provider '{self._config.llm_provider}'"
            )

        prompt = PROMPTS.get(language, PROMPTS["Azerbaijani"])
        prompt += DETAIL_MODIFIERS.get(detail, "")
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES):
            try:
                if self._config.llm_provider == "openai":
                    return self._call_openai(transcript, prompt)
                elif self._config.llm_provider == "anthropic":
                    return self._call_anthropic(transcript, prompt)
                else:
                    raise ValueError(
                        f"Unknown LLM provider: {self._config.llm_provider}"
                    )
            except Exception as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAYS[attempt]
                    logger.warning(
                        "LLM API call failed (attempt %d/%d): %s. Retrying in %ds...",
                        attempt + 1,
                        MAX_RETRIES,
                        e,
                        delay,
                    )
                    time.sleep(delay)

        raise RuntimeError(
            f"LLM API failed after {MAX_RETRIES} attempts: {last_error}"
        )

    def _call_openai(self, transcript: str, system_prompt: str) -> str:
        from openai import OpenAI

        client = OpenAI(api_key=self._config.openai_api_key)
        response = client.chat.completions.create(
            model=self._config.llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Meeting transcript:\n\n{transcript}"},
            ],
            temperature=0.3,
            max_tokens=4096,
        )
        content = response.choices[0].message.content
        assert content is not None
        return content

    def _call_anthropic(self, transcript: str, system_prompt: str) -> str:
        import anthropic

        client = anthropic.Anthropic(api_key=self._config.anthropic_api_key)
        response = client.messages.create(
            model=self._config.llm_model,
            max_tokens=4096,
            system=system_prompt,
            messages=[
                {"role": "user", "content": f"Meeting transcript:\n\n{transcript}"},
            ],
            temperature=0.3,
        )
        return response.content[0].text


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Usage: python summarizer.py <transcript.txt> [language]")
        sys.exit(1)

    from pathlib import Path

    cfg = Config.from_env()
    s = Summarizer(cfg)
    transcript_text = Path(sys.argv[1]).read_text(encoding="utf-8")
    lang = sys.argv[2] if len(sys.argv) > 2 else "Azerbaijani"
    result = s.summarize(transcript_text, language=lang)
    print(f"\n--- Summary ({lang}) ---")
    print(result)
