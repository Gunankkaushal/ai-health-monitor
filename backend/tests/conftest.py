"""Test configuration.

Uses an isolated temporary SQLite file and forces demo mode with an empty Groq key,
so tests never touch the real database or the Groq API — even after a key is added to .env.
"""

import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="health_tests_"))
os.environ["DATABASE_URL"] = "sqlite:///" + (_TMP / "test.db").as_posix()
os.environ["GROQ_API_KEY"] = ""
os.environ["DEMO_MODE"] = "true"

import pytest  # noqa: E402

from app.mcp_client import MCPManager  # noqa: E402
from app.seed import seed  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def seeded_db():
    seed(reset=True)
    yield


@asynccontextmanager
async def running_manager(**kwargs):
    manager = MCPManager(**kwargs)
    await manager.start()
    try:
        yield manager
    finally:
        await manager.stop()


@pytest.fixture
async def manager():
    async with running_manager() as m:
        yield m
