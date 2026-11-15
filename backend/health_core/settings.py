"""Environment-driven settings shared by the backend and the MCP server processes."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent

# The single place secrets live: <project root>/.env  (never committed).
load_dotenv(PROJECT_ROOT / ".env", override=False)

DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"


def _truthy(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def resolve_database_url(raw: str | None = None) -> str:
    """Return an absolute SQLite URL so every process (backend + MCP servers) shares one file.

    Relative paths like ``sqlite:///./health_monitoring.db`` are resolved against the
    backend directory rather than whatever the current working directory happens to be.
    """
    url = raw or os.environ.get("DATABASE_URL") or "sqlite:///./health_monitoring.db"
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise ValueError("Only SQLite DATABASE_URLs (sqlite:///...) are supported in this project.")
    path_part = url[len(prefix):]
    if path_part == ":memory:":
        raise ValueError("In-memory SQLite cannot be shared with MCP server processes.")
    path = Path(path_part)
    if not path.is_absolute():
        path = (BACKEND_DIR / path).resolve()
    return f"{prefix}{path.as_posix()}"


def rules_config_path() -> Path:
    override = os.environ.get("RULES_CONFIG_PATH")
    return Path(override) if override else BACKEND_DIR / "health_core" / "rules_config.json"


def groq_api_key() -> str:
    return (os.environ.get("GROQ_API_KEY") or "").strip()


def groq_model() -> str:
    return (os.environ.get("GROQ_MODEL") or "").strip() or DEFAULT_GROQ_MODEL


def demo_mode() -> bool:
    return _truthy(os.environ.get("DEMO_MODE"), default=True)
