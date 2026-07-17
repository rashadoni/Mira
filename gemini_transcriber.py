"""Gemini-based transcription for accurate Azerbaijani + Russian recognition."""
from __future__ import annotations

import logging
import threading
import wave
from collections.abc import Callable
from pathlib import Path

from config import Config

logger = logging.getLogger(__name__)

TRANSCRIBE_PROMPT = """\
Transcribe this audio. Output TWO sections separated by ===RAWTEXT===

SECTION 1 — with timestamps:
[MM:SS] exact words spoken

SECTION 2 (after ===RAWTEXT===) — same text without timestamps, plain paragraphs.

CRITICAL RULES:
- NEVER translate. NEVER make up text. ONLY write what you ACTUALLY HEAR.
- If someone speaks Russian — write in Russian Cyrillic.
- If someone speaks Azerbaijani — write in Azerbaijani Latin.
- If someone speaks English — write in English.
- Do NOT convert Azerbaijani to Turkish.
- If you cannot hear clearly, write [unclear] — do NOT guess or hallucinate.
- If there is silence, write [silence] — do NOT invent speech.

Example output:
[00:00] Здравствуйте, давайте начнём.
[00:05] Bu layihə haqqında danışaq.
[00:12] Okay, let's discuss the timeline.
===RAWTEXT===
Здравствуйте, давайте начнём.
Bu layihə haqqında danışaq.
Okay, let's discuss the timeline.
"""


class GeminiTranscriber:
    """Transcribes audio using Google Gemini API.

    Advantages over Whisper:
    - Much better Azerbaijani recognition
    - Faster (cloud GPU)
    - Speaker diarization built-in
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._client = None

    def _get_client(self):
        if self._client is None:
            from google import genai
            self._client = genai.Client(api_key=self._config.gemini_api_key)
        return self._client

    @staticmethod
    def _get_wav_duration(audio_path: Path) -> float:
        try:
            with wave.open(str(audio_path), "rb") as wf:
                return wf.getnframes() / wf.getframerate()
        except Exception:
            return 0.0

    def transcribe(
        self,
        audio_path: Path,
        on_progress: Callable[[float], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> tuple[str, str]:
        """Transcribe audio via Gemini API.

        Returns (display_transcript, raw_transcript).
        """
        if not self._config.has_gemini_key:
            raise ValueError("GEMINI_API_KEY not set")

        client = self._get_client()
        duration = self._get_wav_duration(audio_path)
        logger.info("Gemini transcribing %s (%.0f sec)...", audio_path, duration)

        if on_progress:
            on_progress(10.0)

        # Upload audio file
        logger.info("Uploading audio to Gemini...")
        uploaded = client.files.upload(file=audio_path)

        if cancel_event and cancel_event.is_set():
            return "", ""

        if on_progress:
            on_progress(40.0)

        # Single API call with retry (Gemini sometimes returns 503)
        import time
        full_text = ""
        for attempt in range(3):
            try:
                logger.info("Requesting transcription (attempt %d)...", attempt + 1)
                response = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=[uploaded, TRANSCRIBE_PROMPT],
                )
                full_text = response.text or ""
                break
            except Exception as e:
                logger.warning("Gemini attempt %d failed: %s", attempt + 1, e)
                if attempt < 2:
                    time.sleep(5 * (attempt + 1))  # 5s, 10s
                else:
                    raise

        if on_progress:
            on_progress(90.0)

        # Parse two sections from response
        if "===RAWTEXT===" in full_text:
            parts = full_text.split("===RAWTEXT===", 1)
            display_text = parts[0].strip()
            raw_text = parts[1].strip()
        else:
            # Fallback — use full text for both
            display_text = full_text.strip()
            # Strip timestamps for raw version
            import re
            raw_text = re.sub(r"\[\d{1,2}:\d{2}\]\s*", "", display_text)

        if on_progress:
            on_progress(100.0)

        logger.info(
            "Gemini transcription complete: %d display chars, %d raw chars",
            len(display_text), len(raw_text),
        )
        return display_text, raw_text


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Usage: python gemini_transcriber.py <audio.wav>")
        sys.exit(1)

    cfg = Config.from_env()
    t = GeminiTranscriber(cfg)
    display, raw = t.transcribe(
        Path(sys.argv[1]),
        on_progress=lambda pct: print(f"  Progress: {pct:.0f}%"),
    )
    print("\n--- Display ---")
    print(display[:500])
    print("\n--- Raw ---")
    print(raw[:500])
