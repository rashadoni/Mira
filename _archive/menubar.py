"""Mira Menu Bar app — launches main Mira with pre-selected engine."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

import rumps


def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        app_dir = Path.home() / "Documents" / "Mira"
    else:
        app_dir = Path(__file__).parent
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


def get_source_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).parent


class MiraMenuBar(rumps.App):
    def __init__(self) -> None:
        super().__init__("●", quit_button=None)
        self._mira_process: subprocess.Popen | None = None

        self.menu = [
            rumps.MenuItem("Start with Whisper (offline)", callback=lambda _: self.launch("whisper")),
            rumps.MenuItem("Start with Whisper GPU (fast)", callback=lambda _: self.launch("whisper_gpu")),
            rumps.MenuItem("Start with Gemini (best for Azerbaijani)", callback=lambda _: self.launch("gemini")),
            None,
            rumps.MenuItem("Open Mira", callback=lambda _: self.open_mira()),
            rumps.MenuItem("Show in Finder", callback=lambda _: self.show_in_finder()),
            None,
            rumps.MenuItem("Quit Mira Menu Bar", callback=lambda _: self.quit_app()),
        ]

    def launch(self, engine: str) -> None:
        """Launch Mira with pre-selected engine."""
        # Write engine choice to .env so main.py picks it up
        app_dir = get_app_dir()
        env_file = app_dir / ".env"
        if env_file.exists():
            lines = env_file.read_text(encoding="utf-8").splitlines()
            new_lines = []
            found = False
            for line in lines:
                if line.startswith("TRANSCRIPTION_ENGINE="):
                    engine_value = "gemini" if engine == "gemini" else ("whisper_gpu" if engine == "whisper_gpu" else "whisper")
                    new_lines.append(f"TRANSCRIPTION_ENGINE={engine_value}")
                    found = True
                else:
                    new_lines.append(line)
            if not found:
                engine_value = "gemini" if engine == "gemini" else ("whisper_gpu" if engine == "whisper_gpu" else "whisper")
                new_lines.append(f"TRANSCRIPTION_ENGINE={engine_value}")
            env_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

        self.open_mira(auto_record=True)

    def open_mira(self, auto_record: bool = False) -> None:
        """Open main Mira app."""
        if self._mira_process and self._mira_process.poll() is None:
            # Already running — just bring to front
            subprocess.run(["osascript", "-e",
                'tell application "Mira" to activate'], capture_output=True)
            return

        # Launch Mira.app
        env = os.environ.copy()
        if auto_record:
            env["MIRA_AUTO_RECORD"] = "1"

        mira_app = "/Applications/Mira.app"
        if Path(mira_app).exists():
            self._mira_process = subprocess.Popen(
                ["open", "-a", mira_app], env=env,
            )
        else:
            rumps.alert("Mira not installed",
                        "Install Mira.app in /Applications first.")

    def show_in_finder(self) -> None:
        subprocess.run(["open", str(get_app_dir() / "recordings")])

    def quit_app(self) -> None:
        rumps.quit_application()


if __name__ == "__main__":
    MiraMenuBar().run()
