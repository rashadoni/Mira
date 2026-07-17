from __future__ import annotations

import json
import logging
import subprocess
import threading
from datetime import datetime
from pathlib import Path

import customtkinter as ctk

from config import Config
from gemini_transcriber import GeminiTranscriber
from recorder import Recorder
from summarizer import Summarizer
from transcriber import Transcriber

logger = logging.getLogger(__name__)


class App(ctk.CTk):
    """Mira — Meeting Recorder with full feature set."""

    STATE_LOADING = "loading"
    STATE_IDLE = "idle"
    STATE_RECORDING = "recording"
    STATE_PAUSED = "paused"
    STATE_TRANSCRIBING = "transcribing"
    STATE_READY = "ready"
    STATE_SUMMARIZING = "summarizing"
    STATE_DONE = "done"

    def __init__(self, config: Config) -> None:
        super().__init__()

        self._config = config
        self._state = self.STATE_LOADING
        self._shutdown_event = threading.Event()
        self._pipeline_thread: threading.Thread | None = None
        self._timer_id: str | None = None
        self._volume_id: str | None = None
        self._pulse_id: str | None = None
        self._session_id: str | None = None
        self._current_transcript: str = ""
        self._current_raw_transcript: str = ""
        self._current_summary: str = ""
        self._showing_summary = False
        self._cancel_event = threading.Event()
        self._last_summary_time: float = 0.0

        # Components
        self._recorder = Recorder(config)
        self._transcriber = Transcriber(config)
        self._gemini_transcriber = GeminiTranscriber(config)
        self._summarizer = Summarizer(config)

        # Window setup
        self.title("Mira")
        self.geometry("540x780")
        self.resizable(False, False)
        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._model_loaded = False

        self._build_ui()
        self._bind_shortcuts()
        self._set_state(self.STATE_IDLE)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)

        # Row 0: Title
        ctk.CTkLabel(
            self, text="Mira", font=ctk.CTkFont(size=28, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(20, 0))

        # Row 1: Subtitle
        ctk.CTkLabel(
            self, text="Meeting Recorder",
            font=ctk.CTkFont(size=13), text_color="gray",
        ).grid(row=1, column=0, padx=20, pady=(0, 10))

        # Row 2: Timer — small and subtle
        self._timer_label = ctk.CTkLabel(
            self, text="00:00",
            font=ctk.CTkFont(size=18, weight="normal"),
            text_color="#888888",
        )
        self._timer_label.grid(row=2, column=0, padx=20, pady=(5, 2))

        # Row 3: Volume meter — thin and subtle
        self._volume_frame = ctk.CTkFrame(self, height=3, corner_radius=2)
        self._volume_frame.grid(row=3, column=0, padx=120, pady=(0, 10), sticky="ew")
        self._volume_frame.grid_propagate(False)

        self._volume_bar = ctk.CTkFrame(
            self._volume_frame, height=3, corner_radius=2,
            fg_color="#555555",
        )
        self._volume_bar.place(relx=0, rely=0, relheight=1.0, relwidth=0.0)

        # Row 4: Record + Pause buttons
        self._btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._btn_frame.grid(row=4, column=0, padx=20, pady=(5, 3))

        self._record_btn = ctk.CTkButton(
            self._btn_frame, text="Record",
            font=ctk.CTkFont(size=13, weight="normal"),
            height=36, width=120, corner_radius=18,
            fg_color="#444444", hover_color="#555555",
            command=self._toggle_record,
        )
        self._record_btn.grid(row=0, column=0, padx=5, pady=3)

        self._pause_btn = ctk.CTkButton(
            self._btn_frame, text="Pause",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=50, width=75, corner_radius=25,
            fg_color="#FFC107", hover_color="#E0A800",
            text_color="black",
            command=self._toggle_pause,
        )

        # Row 5: Engine selector with explanation
        self._engine_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._engine_frame.grid(row=5, column=0, padx=30, pady=(0, 3))

        engine_map = {
            "whisper": "Whisper",
            "whisper_gpu": "Whisper GPU",
            "whisper gpu": "Whisper GPU",
            "gemini": "Gemini",
        }
        default_engine = engine_map.get(
            self._config.transcription_engine.lower(), "Whisper"
        )
        self._engine_var = ctk.StringVar(value=default_engine)

        self._engine_menu = ctk.CTkSegmentedButton(
            self._engine_frame,
            values=["Whisper", "Whisper GPU", "Gemini"],
            variable=self._engine_var,
            font=ctk.CTkFont(size=12),
            height=28, corner_radius=14,
            command=self._on_engine_change,
        )
        self._engine_menu.grid(row=0, column=0, padx=5, pady=(0, 2))

        self._engine_hint = ctk.CTkLabel(
            self._engine_frame, text="GPU — fast & free  |  Gemini — best for Azerbaijani",
            font=ctk.CTkFont(size=10), text_color="#888888",
        )
        self._engine_hint.grid(row=1, column=0, padx=5)

        # Row 6: Summary frame (hidden by default)
        self._summary_frame = ctk.CTkFrame(self, fg_color="transparent")

        self._summary_btn = ctk.CTkButton(
            self._summary_frame, text="Summarize",
            font=ctk.CTkFont(size=14, weight="bold"),
            height=38, width=140, corner_radius=19,
            fg_color="#0D6EFD", hover_color="#0B5ED7",
            command=self._request_summary,
        )
        self._summary_btn.grid(row=0, column=0, padx=(0, 6))

        self._lang_var = ctk.StringVar(value="Azerbaijani")
        self._lang_menu = ctk.CTkOptionMenu(
            self._summary_frame,
            values=["Azerbaijani", "Russian", "English", "Turkish"],
            variable=self._lang_var,
            font=ctk.CTkFont(size=12),
            height=38, width=120, corner_radius=19,
            fg_color="#6C757D", button_color="#5A6268",
            button_hover_color="#4E555B",
        )
        self._lang_menu.grid(row=0, column=1)

        self._detail_var = ctk.StringVar(value="Standard")
        self._detail_menu = ctk.CTkOptionMenu(
            self._summary_frame,
            values=["Brief", "Standard", "Detailed"],
            variable=self._detail_var,
            font=ctk.CTkFont(size=12),
            height=38, width=100, corner_radius=19,
            fg_color="#6C757D", button_color="#5A6268",
            button_hover_color="#4E555B",
        )
        self._detail_menu.grid(row=0, column=2, padx=(6, 0))

        # Row 7: Title entry (hidden by default)
        self._title_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._title_entry = ctk.CTkEntry(
            self._title_frame, placeholder_text="Name this recording...",
            font=ctk.CTkFont(size=12), height=32, width=300,
            corner_radius=8,
        )
        self._title_entry.grid(row=0, column=0, padx=(0, 5))
        self._title_save_btn = ctk.CTkButton(
            self._title_frame, text="Save",
            font=ctk.CTkFont(size=11), height=32, width=55,
            corner_radius=8,
            fg_color="#28A745", hover_color="#218838",
            command=self._save_title,
        )
        self._title_save_btn.grid(row=0, column=1)

        # Row 8: Status
        self._status_label = ctk.CTkLabel(
            self, text="Ready", font=ctk.CTkFont(size=12), text_color="#888888",
        )
        self._status_label.grid(row=8, column=0, padx=20, pady=(3, 0), sticky="n")

        # Row 9: Tab buttons (hidden by default)
        self._tab_frame = ctk.CTkFrame(self, fg_color="transparent")

        self._tab_transcript_btn = ctk.CTkButton(
            self._tab_frame, text="Transcript",
            font=ctk.CTkFont(size=12, weight="bold"),
            height=28, width=110, corner_radius=14,
            fg_color="#0D6EFD", hover_color="#0B5ED7",
            command=self._show_transcript_tab,
        )
        self._tab_transcript_btn.grid(row=0, column=0, padx=2)

        self._tab_summary_btn = ctk.CTkButton(
            self._tab_frame, text="Summary",
            font=ctk.CTkFont(size=12), height=28, width=110,
            corner_radius=14,
            fg_color="#3a3a3a", hover_color="#555555",
            state="disabled",
            command=self._show_summary_tab,
        )
        self._tab_summary_btn.grid(row=0, column=1, padx=2)

        self._copy_btn = ctk.CTkButton(
            self._tab_frame, text="Copy",
            font=ctk.CTkFont(size=12), height=28, width=60,
            corner_radius=14,
            fg_color="#6C757D", hover_color="#5A6268",
            command=self._copy_to_clipboard,
        )
        self._copy_btn.grid(row=0, column=2, padx=2)

        # Row 10: Output textbox
        self._output_text = ctk.CTkTextbox(
            self, font=ctk.CTkFont(size=13), wrap="word",
            state="disabled", corner_radius=8,
        )
        self._output_text.grid(
            row=10, column=0, padx=20, pady=(6, 6), sticky="nsew",
        )
        self.grid_rowconfigure(10, weight=1)

        # Row 11: Bottom bar
        self._bottom_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._bottom_frame.grid(row=11, column=0, padx=20, pady=(0, 10))

        self._open_finder_btn = ctk.CTkButton(
            self._bottom_frame, text="📁  Files",
            font=ctk.CTkFont(size=11), height=26, width=80,
            corner_radius=13,
            fg_color="#444444", hover_color="#555555",
            command=self._open_in_finder,
        )
        self._open_finder_btn.grid(row=0, column=0, padx=4)

        self._history_btn = ctk.CTkButton(
            self._bottom_frame, text="📋  History",
            font=ctk.CTkFont(size=11), height=26, width=90,
            corner_radius=13,
            fg_color="#444444", hover_color="#555555",
            command=self._show_history,
        )
        self._history_btn.grid(row=0, column=1, padx=4)

    def _on_engine_change(self, value: str) -> None:
        """Update hint text when engine changes."""
        hints = {
            "Whisper": "CPU — slow but works offline, free",
            "Whisper GPU": "Apple Silicon GPU — fast & free, offline",
            "Gemini": "Cloud AI — best for Azerbaijani, needs internet",
        }
        self._engine_hint.configure(text=hints.get(value, ""))

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    def _set_state(self, state: str) -> None:
        self._state = state

        status_map = {
            self.STATE_LOADING: "Loading model...",
            self.STATE_IDLE: "Ready",
            self.STATE_RECORDING: "Recording...",
            self.STATE_PAUSED: "Paused",
            self.STATE_TRANSCRIBING: "Transcribing...",
            self.STATE_READY: "Transcript ready",
            self.STATE_SUMMARIZING: "Summarizing...",
            self.STATE_DONE: "Done!",
        }
        self._status_label.configure(text=status_map.get(state, state))

        # Record button — subtle grey idle, slightly darker when recording
        if state == self.STATE_LOADING:
            self._record_btn.configure(text="Loading...", state="disabled", fg_color="#333333")
        elif state == self.STATE_IDLE:
            self._record_btn.configure(
                text="Record", state="normal",
                fg_color="#444444", hover_color="#555555",
            )
            self._timer_label.configure(text="00:00", text_color="#888888")
        elif state in (self.STATE_RECORDING, self.STATE_PAUSED):
            self._record_btn.configure(
                text="Stop", state="normal",
                fg_color="#5A6268", hover_color="#6A7278",
            )
        elif state in (self.STATE_TRANSCRIBING, self.STATE_SUMMARIZING):
            self._record_btn.configure(text="Processing...", state="disabled", fg_color="#333333")
        elif state in (self.STATE_READY, self.STATE_DONE):
            self._record_btn.configure(
                text="New Recording", state="normal",
                fg_color="#444444", hover_color="#555555",
            )

        # Pause button
        if state == self.STATE_RECORDING:
            self._pause_btn.configure(text="Pause", fg_color="#FFC107")
            self._pause_btn.grid(row=0, column=1, padx=5, pady=5)
        elif state == self.STATE_PAUSED:
            self._pause_btn.configure(text="Resume", fg_color="#28A745")
            self._pause_btn.grid(row=0, column=1, padx=5, pady=5)
        else:
            self._pause_btn.grid_remove()

        # Summary frame (button + language) — show in READY and DONE
        if state == self.STATE_READY:
            self._summary_btn.configure(text="Summarize  (⌘S)", state="normal")
            self._lang_menu.configure(state="normal")
            self._summary_frame.grid(row=6, column=0, padx=20, pady=(3, 3))
        elif state == self.STATE_DONE:
            self._summary_btn.configure(text="Re-summarize", state="normal")
            self._lang_menu.configure(state="normal")
            self._summary_frame.grid(row=6, column=0, padx=20, pady=(3, 3))
        elif state == self.STATE_SUMMARIZING:
            self._summary_btn.configure(text="Summarizing...", state="disabled")
            self._lang_menu.configure(state="disabled")
        else:
            self._summary_frame.grid_remove()

        # Title entry (show after transcription, below summary)
        if state == self.STATE_READY:
            self._title_entry.delete(0, "end")
            self._title_frame.grid(row=7, column=0, padx=20, pady=(0, 0))
        else:
            self._title_frame.grid_remove()

        # Tab frame + Summary tab enabled/disabled
        if state in (self.STATE_READY, self.STATE_DONE):
            self._tab_frame.grid(row=9, column=0, padx=20, pady=(3, 0))
            if self._current_summary:
                self._tab_summary_btn.configure(state="normal", fg_color="gray")
            else:
                self._tab_summary_btn.configure(state="disabled", fg_color="#3a3a3a")
        else:
            self._tab_frame.grid_remove()

        # Pulse animation
        if state == self.STATE_RECORDING:
            self._start_pulse()
        else:
            self._stop_pulse()

        # Engine selector — hide during recording/processing
        if state in (self.STATE_IDLE, self.STATE_READY, self.STATE_DONE):
            self._engine_frame.grid()
        else:
            self._engine_frame.grid_remove()

        # Volume meter visibility
        if state in (self.STATE_RECORDING, self.STATE_PAUSED):
            self._volume_frame.grid()
        else:
            self._volume_bar.place(relwidth=0.0)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def _toggle_record(self) -> None:
        if self._state in (self.STATE_RECORDING, self.STATE_PAUSED):
            self._stop_recording()
        elif self._state in (self.STATE_IDLE, self.STATE_READY, self.STATE_DONE):
            self._start_recording()

    def _start_recording(self) -> None:
        logger.info("_start_recording called, state=%s", self._state)
        self._do_start_recording()

    def _do_start_recording(self) -> None:
        """Actually start recording after confirmation."""
        self._clear_output()
        self._current_transcript = ""
        self._current_raw_transcript = ""
        self._current_summary = ""
        self._showing_summary = False
        self._cancel_event.clear()

        engine = self._engine_var.get().lower()

        # Gemini and MLX don't need preloaded model — start immediately
        if engine in ("gemini", "whisper gpu"):
            self._begin_recording()
            return

        # Whisper needs model loaded first
        if not self._model_loaded:
            self._set_state(self.STATE_LOADING)
            threading.Thread(target=self._load_and_record, daemon=True).start()
            return

        self._begin_recording()

    def _load_and_record(self) -> None:
        try:
            self._transcriber.preload()
            self._model_loaded = True
            self.after(0, self._begin_recording)
        except Exception as e:
            logger.error("Failed to load model: %s", e)
            self.after(0, self._show_error, f"Model error: {e}")

    def _begin_recording(self) -> None:
        self._session_id = self._recorder.start()
        self._set_state(self.STATE_RECORDING)
        self._start_timer()
        self._start_volume_meter()

    def _stop_recording(self) -> None:
        self._stop_timer()
        self._stop_volume_meter()
        wav_path = self._recorder.stop()
        self._set_state(self.STATE_TRANSCRIBING)

        self._pipeline_thread = threading.Thread(
            target=self._transcribe_worker, args=(wav_path,), daemon=True,
        )
        self._pipeline_thread.start()

    def _toggle_pause(self) -> None:
        if self._state == self.STATE_RECORDING:
            self._recorder.pause()
            self._set_state(self.STATE_PAUSED)
        elif self._state == self.STATE_PAUSED:
            self._recorder.resume()
            self._set_state(self.STATE_RECORDING)

    # ------------------------------------------------------------------
    # Timer, Volume & Pulse
    # ------------------------------------------------------------------

    def _start_timer(self) -> None:
        self._update_timer()

    def _stop_timer(self) -> None:
        if self._timer_id is not None:
            self.after_cancel(self._timer_id)
            self._timer_id = None

    def _update_timer(self) -> None:
        if self._state not in (self.STATE_RECORDING, self.STATE_PAUSED):
            return
        elapsed = int(self._recorder.elapsed_seconds)
        minutes, seconds = divmod(elapsed, 60)
        self._timer_label.configure(text=f"{minutes:02d}:{seconds:02d}")
        self._timer_id = self.after(1000, self._update_timer)

    def _start_volume_meter(self) -> None:
        self._update_volume()

    def _stop_volume_meter(self) -> None:
        if self._volume_id is not None:
            self.after_cancel(self._volume_id)
            self._volume_id = None

    def _update_volume(self) -> None:
        if self._state not in (self.STATE_RECORDING, self.STATE_PAUSED):
            return
        level = self._recorder.volume_level
        self._volume_bar.place(relx=0, rely=0, relheight=1.0, relwidth=level)
        # Subtle grey tones — no loud colors
        self._volume_bar.configure(fg_color="#666666")
        self._volume_id = self.after(100, self._update_volume)

    def _start_pulse(self) -> None:
        self._pulse_on = True
        self._do_pulse()

    def _stop_pulse(self) -> None:
        if self._pulse_id is not None:
            self.after_cancel(self._pulse_id)
            self._pulse_id = None
        self._timer_label.configure(text_color="#888888")

    def _do_pulse(self) -> None:
        if self._state != self.STATE_RECORDING:
            return
        # Subtle pulse — dim vs slightly less dim
        self._pulse_on = not self._pulse_on
        color = "#999999" if self._pulse_on else "#666666"
        self._timer_label.configure(text_color=color)
        self._pulse_id = self.after(1200, self._do_pulse)  # Slower pulse

    # ------------------------------------------------------------------
    # Transcription
    # ------------------------------------------------------------------

    def _update_transcribe_progress(self, pct: float) -> None:
        def _update(p: float = pct) -> None:
            self._status_label.configure(text=f"Transcribing... {p:.0f}%")
            self._volume_bar.configure(fg_color="#0D6EFD")
            self._volume_bar.place(relx=0, rely=0, relheight=1.0, relwidth=p / 100.0)
        self.after(0, _update)

    def _transcribe_worker(self, wav_path: Path) -> None:
        session_id = self._session_id
        output_dir = self._config.output_dir
        engine = self._engine_var.get().lower()

        try:
            if engine == "gemini" and self._config.has_gemini_key:
                display, raw = self._gemini_transcriber.transcribe(
                    wav_path,
                    on_progress=self._update_transcribe_progress,
                    cancel_event=self._cancel_event,
                )
            elif engine == "whisper gpu":
                display, raw = self._transcribe_mlx(wav_path)
            else:
                # Lazy load whisper model if needed
                if not self._model_loaded:
                    self.after(0, self._status_label.configure, {"text": "Loading Whisper model..."})
                    self._transcriber.preload()
                    self._model_loaded = True
                display, raw = self._transcriber.transcribe(
                    wav_path,
                    on_progress=self._update_transcribe_progress,
                    cancel_event=self._cancel_event,
                )
            self._current_transcript = display
            self._current_raw_transcript = raw

            transcript_path = output_dir / f"{session_id}_transcript.txt"
            transcript_path.write_text(display, encoding="utf-8")
            raw_path = output_dir / f"{session_id}_raw.txt"
            raw_path.write_text(raw, encoding="utf-8")

            meta = {
                "id": session_id,
                "title": "",
                "created_at": datetime.now().isoformat(),
                "duration_seconds": round(self._recorder.elapsed_seconds, 1),
                "wav_path": wav_path.name,
                "transcript_path": f"{session_id}_transcript.txt",
                "engine": engine,
                "whisper_model": self._config.whisper_model_size if engine == "whisper" else None,
                "dropped_frames": self._recorder.dropped_frames,
            }
            meta_path = output_dir / f"{session_id}_meta.json"
            meta_path.write_text(
                json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
            )

            display = display if display.strip() else "[No speech detected]"
            self.after(0, self._show_transcript_result, display)

        except Exception as e:
            logger.error("Transcription error: %s", e, exc_info=True)
            self.after(0, self._show_error, f"Transcription error: {e}")

    # ------------------------------------------------------------------
    # MLX Whisper (Apple GPU — 10x faster)
    # ------------------------------------------------------------------

    def _transcribe_mlx(self, wav_path: Path) -> tuple[str, str]:
        """Transcribe using mlx-whisper on Apple Silicon GPU."""
        import mlx_whisper

        self.after(0, self._status_label.configure, {"text": "Transcribing (GPU)..."})

        result = mlx_whisper.transcribe(
            str(wav_path),
            path_or_hf_repo=f"mlx-community/whisper-{self._config.whisper_model_size}-mlx",
        )

        full_text = result.get("text", "")
        segments = result.get("segments", [])

        display_lines = []
        raw_lines = []
        for seg in segments:
            text = seg.get("text", "").strip()
            if text:
                start = seg.get("start", 0)
                mins = int(start) // 60
                secs = int(start) % 60
                display_lines.append(f"[{mins:02d}:{secs:02d}] {text}")
                raw_lines.append(text)

        return "\n".join(display_lines), "\n".join(raw_lines)

    # ------------------------------------------------------------------
    # Recording title
    # ------------------------------------------------------------------

    def _save_title(self) -> None:
        title = self._title_entry.get().strip()
        if not title or not self._session_id:
            return
        output_dir = self._config.output_dir
        meta_path = output_dir / f"{self._session_id}_meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["title"] = title
            meta_path.write_text(
                json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        self._title_save_btn.configure(text="Saved!")
        self.after(1500, lambda: self._title_save_btn.configure(text="Save"))

    # ------------------------------------------------------------------
    # Summarization
    # ------------------------------------------------------------------

    def _request_summary(self, event=None) -> None:
        if not self._current_raw_transcript.strip():
            return
        if not self._config.has_api_key:
            self._show_error("API key not set — add ANTHROPIC_API_KEY to .env")
            return

        # Rate limiting — 10 second cooldown
        import time
        now = time.time()
        if now - self._last_summary_time < 10:
            return
        self._last_summary_time = now

        self._summary_lang = self._lang_var.get()
        self._summary_detail = self._detail_var.get()
        self._set_state(self.STATE_SUMMARIZING)
        # Internet check + summarize in background thread (never block UI)
        threading.Thread(target=self._summarize_with_check, daemon=True).start()

    def _summarize_with_check(self) -> None:
        """Check internet then summarize — runs in background thread."""
        if not self._check_internet():
            self.after(0, self._show_error, "No internet connection — cannot summarize")
            return
        self._summarize_worker()

    def _summarize_worker(self) -> None:
        session_id = self._session_id
        output_dir = self._config.output_dir

        try:
            summary = self._summarizer.summarize(self._current_raw_transcript, language=self._summary_lang, detail=self._summary_detail)
            self._current_summary = summary

            summary_path = output_dir / f"{session_id}_summary.txt"
            summary_path.write_text(summary, encoding="utf-8")

            meta_path = output_dir / f"{session_id}_meta.json"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                meta["summary_path"] = f"{session_id}_summary.txt"
                meta["llm_provider"] = self._config.llm_provider
                meta["llm_model"] = self._config.llm_model
                meta_path.write_text(
                    json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
                )

            self.after(0, self._show_summary_result, summary)

        except Exception as e:
            logger.error("Summarization failed: %s", e, exc_info=True)
            self.after(0, self._show_error, f"Summarization error: {e}")

    # ------------------------------------------------------------------
    # Tab switching
    # ------------------------------------------------------------------

    def _show_transcript_tab(self) -> None:
        self._showing_summary = False
        self._tab_transcript_btn.configure(fg_color="#0D6EFD")
        self._tab_summary_btn.configure(
            fg_color="gray" if self._current_summary else "#3a3a3a"
        )
        self._set_text(self._current_transcript or "[No transcript]")

    def _show_summary_tab(self) -> None:
        if not self._current_summary:
            return
        self._showing_summary = True
        self._tab_summary_btn.configure(fg_color="#0D6EFD")
        self._tab_transcript_btn.configure(fg_color="gray")
        self._set_text(self._current_summary)

    # ------------------------------------------------------------------
    # Copy
    # ------------------------------------------------------------------

    def _copy_to_clipboard(self, event=None) -> None:
        text = self._current_summary if self._showing_summary else self._current_transcript
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            self._copy_btn.configure(text="Copied!")
            self.after(1500, lambda: self._copy_btn.configure(text="Copy"))

    # ------------------------------------------------------------------
    # Confirmation dialog
    # ------------------------------------------------------------------

    def _show_confirm_dialog(self, title: str, message: str, on_yes: callable) -> None:
        dialog = ctk.CTkToplevel(self)
        dialog.title(title)
        dialog.geometry("350x150")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()

        ctk.CTkLabel(
            dialog, text=message, font=ctk.CTkFont(size=14),
            wraplength=300,
        ).pack(padx=20, pady=(25, 15))

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=(0, 15))

        def _yes() -> None:
            dialog.destroy()
            on_yes()

        ctk.CTkButton(
            btn_frame, text="Yes", width=80, height=32,
            fg_color="#DC3545", hover_color="#C82333",
            command=_yes,
        ).grid(row=0, column=0, padx=10)

        ctk.CTkButton(
            btn_frame, text="Cancel", width=80, height=32,
            fg_color="#6C757D", hover_color="#5A6268",
            command=dialog.destroy,
        ).grid(row=0, column=1, padx=10)

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def _show_history(self) -> None:
        output_dir = self._config.output_dir
        if not output_dir.exists():
            self._set_text("[No recordings yet]")
            return

        # Find all recordings — both with meta and WAV-only (failed transcriptions)
        meta_files = sorted(output_dir.glob("*_meta.json"), reverse=True)[:15]
        wav_files = sorted(output_dir.glob("*.wav"), reverse=True)[:15]

        # Find WAVs without meta (orphaned recordings)
        meta_ids = set()
        for mf in meta_files:
            try:
                meta_ids.add(json.loads(mf.read_text(encoding="utf-8")).get("id", ""))
            except Exception:
                pass
        # Filter out: dotfiles (temp normalized), empty files, and files already in meta
        orphan_wavs = [
            w for w in wav_files
            if w.stem not in meta_ids
            and not w.name.startswith(".")
            and w.stat().st_size > 1024  # Skip tiny/empty WAVs
        ]

        if not meta_files and not orphan_wavs:
            self._set_text("[No recordings yet]")
            return

        if hasattr(self, "_history_win") and self._history_win is not None:
            try:
                self._history_win.destroy()
            except Exception:
                pass

        self._history_win = ctk.CTkToplevel(self)
        self._history_win.title("History")
        self._history_win.geometry("500x520")
        self._history_win.resizable(False, True)
        self._history_win.transient(self)

        ctk.CTkLabel(
            self._history_win, text="Recording History",
            font=ctk.CTkFont(size=20, weight="bold"),
        ).pack(padx=20, pady=(15, 10))

        scroll = ctk.CTkScrollableFrame(self._history_win, corner_radius=8)
        scroll.pack(padx=15, pady=(0, 15), fill="both", expand=True)

        for mf in meta_files:
            try:
                meta = json.loads(mf.read_text(encoding="utf-8"))
                rec_id = meta.get("id", "?")
                title = meta.get("title", "")
                duration = meta.get("duration_seconds", 0)
                mins = int(duration) // 60
                secs = int(duration) % 60
                has_summary = bool(meta.get("summary_path"))

                entry = ctk.CTkFrame(scroll, corner_radius=8)
                entry.pack(fill="x", padx=5, pady=4)
                entry.grid_columnconfigure(1, weight=1)

                # Show title if exists, otherwise timestamp
                display_name = title if title else rec_id
                ctk.CTkLabel(
                    entry, text=display_name,
                    font=ctk.CTkFont(size=13, weight="bold"),
                ).grid(row=0, column=0, columnspan=2, padx=10, pady=(8, 0), sticky="w")

                info_parts = [f"{mins:02d}:{secs:02d}"]
                if title:
                    info_parts.append(rec_id)
                if has_summary:
                    info_parts.append("+ summary")
                ctk.CTkLabel(
                    entry, text="  |  ".join(info_parts),
                    font=ctk.CTkFont(size=11), text_color="gray",
                ).grid(row=1, column=0, columnspan=2, padx=10, pady=(0, 2), sticky="w")

                btn_frame = ctk.CTkFrame(entry, fg_color="transparent")
                btn_frame.grid(row=2, column=0, columnspan=2, padx=8, pady=(2, 8), sticky="w")

                # Check if transcript exists and has content
                t_path = output_dir / f"{rec_id}_transcript.txt"
                has_transcript = t_path.exists() and t_path.stat().st_size > 10

                if has_transcript:
                    ctk.CTkButton(
                        btn_frame, text="Open Transcript",
                        font=ctk.CTkFont(size=12), height=28, width=130,
                        corner_radius=14,
                        fg_color="#0D6EFD", hover_color="#0B5ED7",
                        command=lambda sid=rec_id: self._load_history_transcript(sid),
                    ).grid(row=0, column=0, padx=3)
                else:
                    ctk.CTkButton(
                        btn_frame, text="▶ Transcribe",
                        font=ctk.CTkFont(size=12), height=28, width=130,
                        corner_radius=14,
                        fg_color="#28A745", hover_color="#218838",
                        command=lambda sid=rec_id: self._retranscribe(sid),
                    ).grid(row=0, column=0, padx=3)

                if has_summary:
                    ctk.CTkButton(
                        btn_frame, text="Open Summary",
                        font=ctk.CTkFont(size=12), height=28, width=130,
                        corner_radius=14,
                        fg_color="#6C757D", hover_color="#5A6268",
                        command=lambda sid=rec_id: self._load_history_summary(sid),
                    ).grid(row=0, column=1, padx=3)

            except Exception:
                continue

        # Show orphan WAVs (recorded but never transcribed)
        for wav in orphan_wavs:
            try:
                size_mb = wav.stat().st_size / 1024 / 1024
                entry = ctk.CTkFrame(scroll, corner_radius=8)
                entry.pack(fill="x", padx=5, pady=4)

                ctk.CTkLabel(
                    entry, text=wav.stem,
                    font=ctk.CTkFont(size=13, weight="bold"),
                ).grid(row=0, column=0, padx=10, pady=(8, 0), sticky="w")

                ctk.CTkLabel(
                    entry, text=f"{size_mb:.0f} MB  |  not transcribed",
                    font=ctk.CTkFont(size=11), text_color="#e94560",
                ).grid(row=1, column=0, padx=10, pady=(0, 2), sticky="w")

                ctk.CTkButton(
                    entry, text="▶ Transcribe",
                    font=ctk.CTkFont(size=12), height=28, width=130,
                    corner_radius=14,
                    fg_color="#28A745", hover_color="#218838",
                    command=lambda sid=wav.stem: self._retranscribe(sid),
                ).grid(row=2, column=0, padx=10, pady=(2, 8), sticky="w")
            except Exception:
                continue

    def _retranscribe(self, session_id: str) -> None:
        """Re-transcribe a WAV file from history."""
        output_dir = self._config.output_dir
        wav_path = output_dir / f"{session_id}.wav"
        if not wav_path.exists():
            self._set_text(f"[WAV not found: {wav_path.name}]")
            return

        # Close history window
        if hasattr(self, "_history_win") and self._history_win:
            self._history_win.destroy()

        self._session_id = session_id
        self._set_state(self.STATE_TRANSCRIBING)

        self._pipeline_thread = threading.Thread(
            target=self._transcribe_worker, args=(wav_path,), daemon=True,
        )
        self._pipeline_thread.start()

    def _load_history_transcript(self, session_id: str) -> None:
        output_dir = self._config.output_dir
        path = output_dir / f"{session_id}_transcript.txt"
        if path.exists():
            text = path.read_text(encoding="utf-8")
            self._current_transcript = text
            # Also load raw if available
            raw_path = output_dir / f"{session_id}_raw.txt"
            if raw_path.exists():
                self._current_raw_transcript = raw_path.read_text(encoding="utf-8")
            else:
                # Fallback: strip timestamps from display version
                import re
                self._current_raw_transcript = re.sub(r"\[\d{2}:\d{2}\] ", "", text)
            self._current_summary = ""
            self._session_id = session_id
            self._showing_summary = False
            self._set_text(text)
            self._set_state(self.STATE_READY)
            if hasattr(self, "_history_win") and self._history_win:
                self._history_win.destroy()
        else:
            self._set_text(f"[Transcript not found: {path.name}]")

    def _load_history_summary(self, session_id: str) -> None:
        output_dir = self._config.output_dir

        t_path = output_dir / f"{session_id}_transcript.txt"
        if t_path.exists():
            self._current_transcript = t_path.read_text(encoding="utf-8")
        raw_path = output_dir / f"{session_id}_raw.txt"
        if raw_path.exists():
            self._current_raw_transcript = raw_path.read_text(encoding="utf-8")

        s_path = output_dir / f"{session_id}_summary.txt"
        if s_path.exists():
            text = s_path.read_text(encoding="utf-8")
            self._current_summary = text
            self._session_id = session_id
            self._showing_summary = True
            self._set_text(text)
            self._set_state(self.STATE_DONE)
            if hasattr(self, "_history_win") and self._history_win:
                self._history_win.destroy()
        else:
            self._set_text(f"[Summary not found: {s_path.name}]")

    # ------------------------------------------------------------------
    # Keyboard shortcuts
    # ------------------------------------------------------------------

    def _bind_shortcuts(self) -> None:
        # NOTE: Space removed — conflicts with button clicks in tkinter
        self.bind("<Escape>", lambda e: self._cancel_action())
        self.bind("<Command-c>", self._copy_to_clipboard)
        self.bind("<Command-s>", self._request_summary)

    def _cancel_action(self) -> None:
        if self._state == self.STATE_TRANSCRIBING:
            self._cancel_event.set()
            self._status_label.configure(text="Cancelling...")

    # ------------------------------------------------------------------
    # Network check
    # ------------------------------------------------------------------

    @staticmethod
    def _check_internet() -> bool:
        import socket
        try:
            socket.create_connection(("api.anthropic.com", 443), timeout=3)
            return True
        except OSError:
            return False

    # ------------------------------------------------------------------
    # Open in Finder
    # ------------------------------------------------------------------

    def _open_in_finder(self) -> None:
        output_dir = self._config.output_dir
        if output_dir.exists():
            subprocess.run(["open", str(output_dir)])

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------

    def _show_transcript_result(self, text: str) -> None:
        self._set_text(text)
        self._showing_summary = False
        self._set_state(self.STATE_READY)

    def _show_summary_result(self, text: str) -> None:
        self._current_summary = text
        self._showing_summary = True
        self._tab_summary_btn.configure(fg_color="#0D6EFD", state="normal")
        self._tab_transcript_btn.configure(fg_color="gray")
        self._set_text(text)
        self._set_state(self.STATE_DONE)

    def _show_error(self, msg: str) -> None:
        self._set_text(msg)
        self._set_state(self.STATE_READY if self._current_transcript else self.STATE_IDLE)

    def _set_text(self, text: str) -> None:
        self._output_text.configure(state="normal")
        self._output_text.delete("1.0", "end")
        self._output_text.insert("1.0", text)
        self._output_text.configure(state="disabled")

    def _clear_output(self) -> None:
        self._set_text("")

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def _on_close(self) -> None:
        # Warn if transcription in progress
        if self._state == self.STATE_TRANSCRIBING:
            self._show_confirm_dialog(
                "Close Mira?",
                "Transcription is in progress. Close anyway?",
                self._force_close,
            )
            return

        self._force_close()

    def _force_close(self) -> None:
        self._shutdown_event.set()
        self._cancel_event.set()
        self._stop_timer()
        self._stop_volume_meter()
        self._stop_pulse()

        if self._state in (self.STATE_RECORDING, self.STATE_PAUSED):
            try:
                self._recorder.stop()
            except Exception:
                pass

        if self._pipeline_thread is not None and self._pipeline_thread.is_alive():
            self._pipeline_thread.join(timeout=3)

        self.destroy()
