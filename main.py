"""Mira — Meeting Recorder for macOS."""
from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path


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


def setup_first_run(app_dir: Path, source_dir: Path) -> None:
    env_file = app_dir / ".env"
    if not env_file.exists():
        example = source_dir / ".env.example"
        if example.exists():
            shutil.copy2(example, env_file)
        else:
            env_file.write_text(
                "LLM_PROVIDER=anthropic\n"
                "ANTHROPIC_API_KEY=\n"
                "GEMINI_API_KEY=\n"
                "TRANSCRIPTION_ENGINE=whisper\n"
                "LLM_MODEL=claude-sonnet-4-20250514\n"
                "WHISPER_MODEL_SIZE=large-v3\n"
                "WHISPER_DEVICE=cpu\n"
                "WHISPER_COMPUTE_TYPE=int8\n"
            )


sys.path.insert(0, str(get_source_dir()))


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    app_dir = get_app_dir()
    source_dir = get_source_dir()
    setup_first_run(app_dir, source_dir)

    from config import Config
    config = Config.from_env(app_dir / ".env")
    config.output_dir = app_dir / "recordings"
    config.output_dir.mkdir(parents=True, exist_ok=True)

    warnings = config.validate()
    for w in warnings:
        logging.warning(w)

    from app import App
    App(config).mainloop()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
