from __future__ import annotations

import logging
import threading
import wave
from collections.abc import Callable
from pathlib import Path

from faster_whisper import WhisperModel

from config import Config

logger = logging.getLogger(__name__)


class Transcriber:
    """Wraps faster-whisper with preloading, VAD filtering and progress tracking."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._model: WhisperModel | None = None

    def preload(self) -> None:
        """Load the Whisper model. Call from a background thread at startup."""
        logger.info(
            "Loading Whisper model '%s' (device=%s, compute=%s)...",
            self._config.whisper_model_size,
            self._config.whisper_device,
            self._config.whisper_compute_type,
        )
        self._model = WhisperModel(
            self._config.whisper_model_size,
            device=self._config.whisper_device,
            compute_type=self._config.whisper_compute_type,
        )
        logger.info("Whisper model loaded successfully")

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @staticmethod
    def _get_wav_duration(audio_path: Path) -> float:
        """Get WAV file duration in seconds."""
        try:
            with wave.open(str(audio_path), "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                return frames / rate if rate > 0 else 0.0
        except Exception:
            return 0.0

    def transcribe(
        self,
        audio_path: Path,
        on_progress: Callable[[float], None] | None = None,
        cancel_event: "threading.Event | None" = None,
    ) -> tuple[str, str]:
        """Transcribe a WAV file.

        Returns (display_transcript, raw_transcript):
          - display_transcript: with [MM:SS] timestamps for UI
          - raw_transcript: plain text for summarization

        on_progress: callback(percent) called with 0.0-100.0.
        cancel_event: if set, transcription stops early and returns what it has so far.
        """
        if self._model is None:
            self.preload()
        assert self._model is not None

        total_duration = self._get_wav_duration(audio_path)
        logger.info("Transcribing %s (%.0f sec)...", audio_path, total_duration)

        # Normalize audio if too quiet
        audio_path_str = str(audio_path)
        try:
            import numpy as np
            import wave as _wave
            wf = _wave.open(audio_path_str, "rb")
            raw = wf.readframes(wf.getnframes())
            params = wf.getparams()
            wf.close()

            data = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
            peak = np.max(np.abs(data))
            if peak > 0 and peak < 16000:
                gain = min(16000.0 / peak, 50.0)  # Up to 50x boost for very quiet mic
                data = np.clip(data * gain, -32767, 32767).astype(np.int16)
                norm_path = audio_path.parent / f".normalized_{audio_path.name}"
                wf2 = _wave.open(str(norm_path), "wb")
                wf2.setparams(params)
                wf2.writeframes(data.tobytes())
                wf2.close()
                audio_path_str = str(norm_path)
                logger.info("Audio normalized: gain=%.1fx (peak was %d)", gain, peak)
        except Exception as e:
            logger.warning("Audio normalization failed: %s", e)

        segments, info = self._model.transcribe(
            audio_path_str,
            language=None,
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=1500,
                threshold=0.3,
                speech_pad_ms=600,
                min_speech_duration_ms=100,
            ),
            beam_size=5,
            no_speech_threshold=0.4,
            condition_on_previous_text=True,
            word_timestamps=False,
        )

        # Cleanup normalized temp file
        try:
            norm_cleanup = audio_path.parent / f".normalized_{audio_path.name}"
            if norm_cleanup.exists():
                norm_cleanup.unlink()
        except Exception:
            pass

        display_lines: list[str] = []
        raw_lines: list[str] = []

        for segment in segments:
            # Check cancellation
            if cancel_event is not None and cancel_event.is_set():
                logger.info("Transcription cancelled at %.1f sec", segment.end)
                break

            text = segment.text.strip()
            if text:
                # Format timestamp [MM:SS]
                mins = int(segment.start) // 60
                secs = int(segment.start) % 60
                display_lines.append(f"[{mins:02d}:{secs:02d}] {text}")
                raw_lines.append(text)

            # Report progress
            if on_progress and total_duration > 0:
                pct = min(99.0, (segment.end / total_duration) * 100.0)
                on_progress(pct)

        if on_progress:
            on_progress(100.0)

        display_transcript = "\n".join(display_lines)
        raw_transcript = "\n".join(raw_lines)

        logger.info(
            "Transcription complete: language=%s (prob=%.2f), %d lines",
            info.language,
            info.language_probability,
            len(raw_lines),
        )
        return display_transcript, raw_transcript


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Usage: python transcriber.py <audio.wav>")
        sys.exit(1)

    cfg = Config()
    t = Transcriber(cfg)
    display, raw = t.transcribe(
        Path(sys.argv[1]),
        on_progress=lambda pct: print(f"  Progress: {pct:.0f}%"),
    )
    print("\n--- Transcript (with timestamps) ---")
    print(display)
