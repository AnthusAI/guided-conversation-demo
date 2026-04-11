"""Load repo-root `.env` before tests so OPENAI_API_KEY is available (override=False)."""

from __future__ import annotations

from pathlib import Path


def pytest_sessionstart(session):
    root = Path(__file__).resolve().parent.parent
    env_path = root / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(env_path, override=False)
