from __future__ import annotations

import logging
import queue
import signal
import struct
import threading
import wave
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd

from config import Config

logger = logging.getLogger(__name__)


class Recorder:
    """Streams microphone audio to a WAV file via a bounded queue.

    Reliability features:
    - Raw PCM written first, converted to WAV on stop (crash-safe)
    - Volume RMS computed outside audio callback (no xruns)
    - Large queue buffer (2000 chunks = ~2 min)
    - Dropped frames tracking
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._queue: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=2000)
        self._volume_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=50)
        self._stop_event = threading.Event()
        self._paused = False
        self._stream: sd.InputStream | None = None
        self._writer_thread: threading.Thread | None = None
        self._volume_thread: threading.Thread | None = None
        self._pcm_path: Path | None = None
        self._wav_path: Path | None = None
        self._start_time: float | None = None
        self._pause_start: float | None = None
        self._total_paused: float = 0.0
        self._duration: float = 0.0
        self._volume_level: float = 0.0
        self._dropped_frames: int = 0
        self._total_frames: int = 0
        self._prev_signal_handlers: dict = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> str:
        """Begin recording. Returns the session id (timestamp string)."""
        self._stop_event.clear()
        self._paused = False
        self._total_paused = 0.0
        self._pause_start = None
        self._volume_level = 0.0
        self._dropped_frames = 0
        self._total_frames = 0

        session_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output_dir = self._config.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        # Write raw PCM first (crash-safe), convert to WAV on stop
        self._pcm_path = output_dir / f".{session_id}.pcm"
        self._wav_path = output_dir / f"{session_id}.wav"

        # Install signal handlers for graceful shutdown
        self._install_signal_handlers()

        self._stream = sd.InputStream(
            samplerate=self._config.sample_rate,
            channels=self._config.channels,
            dtype="int16",
            blocksize=1024,
            callback=self._audio_callback,
        )

        self._writer_thread = threading.Thread(
            target=self._writer_loop, daemon=True
        )
        self._volume_thread = threading.Thread(
            target=self._volume_loop, daemon=True
        )
        self._writer_thread.start()
        self._volume_thread.start()
        self._stream.start()
        self._start_time = datetime.now().timestamp()

        logger.info("Recording started → %s", self._pcm_path)
        return session_id

    def stop(self) -> Path:
        """Stop recording, convert PCM to WAV, return the WAV path."""
        if self._paused:
            self._unpause_internal()

        self._stop_event.set()
        self._restore_signal_handlers()

        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        # Signal threads to exit
        self._queue.put(None)

        if self._writer_thread is not None:
            self._writer_thread.join(timeout=5)
            self._writer_thread = None

        if self._volume_thread is not None:
            self._volume_thread.join(timeout=2)
            self._volume_thread = None

        if self._start_time is not None:
            self._duration = datetime.now().timestamp() - self._start_time - self._total_paused
            self._start_time = None

        # Convert raw PCM → WAV
        self._pcm_to_wav()

        self._volume_level = 0.0

        if self._dropped_frames > 0:
            logger.warning(
                "Dropped %d/%d frames (%.1f%%)",
                self._dropped_frames,
                self._total_frames,
                100 * self._dropped_frames / max(1, self._total_frames),
            )

        logger.info("Recording stopped (%.1fs) → %s", self._duration, self._wav_path)
        assert self._wav_path is not None
        return self._wav_path

    def pause(self) -> None:
        if not self._paused:
            self._paused = True
            self._pause_start = datetime.now().timestamp()
            self._volume_level = 0.0
            logger.info("Recording paused")

    def resume(self) -> None:
        if self._paused:
            self._unpause_internal()
            logger.info("Recording resumed")

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def volume_level(self) -> float:
        return self._volume_level

    @property
    def elapsed_seconds(self) -> float:
        if self._start_time is not None:
            paused_now = self._total_paused
            if self._paused and self._pause_start is not None:
                paused_now += datetime.now().timestamp() - self._pause_start
            return datetime.now().timestamp() - self._start_time - paused_now
        return self._duration

    @property
    def dropped_frames(self) -> int:
        return self._dropped_frames

    # ------------------------------------------------------------------
    # Signal handlers (crash protection)
    # ------------------------------------------------------------------

    def _install_signal_handlers(self) -> None:
        """Install handlers to cleanly stop recording on SIGTERM/SIGINT."""
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                self._prev_signal_handlers[sig] = signal.getsignal(sig)
                signal.signal(sig, self._signal_handler)
            except (OSError, ValueError):
                pass

    def _restore_signal_handlers(self) -> None:
        for sig, handler in self._prev_signal_handlers.items():
            try:
                signal.signal(sig, handler)
            except (OSError, ValueError):
                pass
        self._prev_signal_handlers.clear()

    def _signal_handler(self, signum: int, frame: object) -> None:
        logger.warning("Signal %d received — setting stop flag", signum)
        self._stop_event.set()  # Only set flag — never call complex methods from signal handler

    # ------------------------------------------------------------------
    # Audio callback (minimal work — no numpy math)
    # ------------------------------------------------------------------

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: object,
        status: sd.CallbackFlags,
    ) -> None:
        if status:
            logger.warning("Audio callback status: %s", status)

        self._total_frames += 1

        # Send copy to volume thread (non-blocking, drop if full)
        try:
            self._volume_queue.put_nowait(indata.copy())
        except queue.Full:
            pass

        if self._paused:
            return

        try:
            self._queue.put_nowait(indata.copy())
        except queue.Full:
            self._dropped_frames += 1
            if self._dropped_frames % 100 == 1:
                logger.warning("Audio queue full — dropped frame #%d", self._dropped_frames)

    # ------------------------------------------------------------------
    # Volume computation (separate thread, not in callback)
    # ------------------------------------------------------------------

    def _volume_loop(self) -> None:
        """Compute RMS volume in a separate thread."""
        while not self._stop_event.is_set():
            try:
                chunk = self._volume_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            rms = np.sqrt(np.mean(chunk.astype(np.float32) ** 2))
            self._volume_level = min(1.0, rms / 8000.0)

    # ------------------------------------------------------------------
    # Writer (raw PCM)
    # ------------------------------------------------------------------

    def _writer_loop(self) -> None:
        assert self._pcm_path is not None
        f = open(self._pcm_path, "wb")

        try:
            while True:
                try:
                    chunk = self._queue.get(timeout=0.5)
                except queue.Empty:
                    if self._stop_event.is_set():
                        break
                    continue

                if chunk is None:
                    break

                f.write(chunk.tobytes())
        finally:
            f.close()
            logger.info("Raw PCM written: %s", self._pcm_path)

    # ------------------------------------------------------------------
    # PCM → WAV conversion
    # ------------------------------------------------------------------

    def _pcm_to_wav(self) -> None:
        """Convert raw PCM file to proper WAV with header."""
        assert self._pcm_path is not None
        assert self._wav_path is not None

        if not self._pcm_path.exists():
            logger.error("PCM file not found: %s", self._pcm_path)
            return

        try:
            pcm_data = self._pcm_path.read_bytes()

            wf = wave.open(str(self._wav_path), "wb")
            wf.setnchannels(self._config.channels)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(self._config.sample_rate)
            wf.writeframes(pcm_data)
            wf.close()

            logger.info("WAV file created: %s (%d bytes)", self._wav_path, len(pcm_data))
        except Exception as e:
            logger.error("PCM to WAV conversion failed: %s (PCM preserved at %s)", e, self._pcm_path)
            return  # Keep PCM for manual recovery

        # Only delete PCM after successful conversion
        try:
            self._pcm_path.unlink()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _unpause_internal(self) -> None:
        if self._pause_start is not None:
            self._total_paused += datetime.now().timestamp() - self._pause_start
            self._pause_start = None
        self._paused = False


if __name__ == "__main__":
    import time

    logging.basicConfig(level=logging.INFO)
    cfg = Config()
    rec = Recorder(cfg)

    print("Recording 5 seconds...")
    rec.start()
    for i in range(50):
        time.sleep(0.1)
        if i % 10 == 0:
            print(f"  Volume: {rec.volume_level:.2f}")
    path = rec.stop()
    print(f"Saved to {path} ({path.stat().st_size / 1024:.1f} KB)")
    print(f"Dropped frames: {rec.dropped_frames}")
