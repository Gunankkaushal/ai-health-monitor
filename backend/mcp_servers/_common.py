"""Shared bootstrap for the stdio MCP servers.

IMPORTANT: on the stdio transport, stdout carries JSON-RPC protocol messages only.
All logging goes to stderr (which the backend redirects to backend/logs/mcp_<name>.log).
Never use print() in these servers.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from typing import Annotated, Any, Literal, TypeVar

from pydantic import Field

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from health_core.db import init_db
from health_core.repository import PatientNotFound

T = TypeVar("T")

PatientId = Annotated[
    int,
    Field(ge=1, le=10_000_000, description="ID of the active patient. Injected by the backend; not chosen by the model."),
]
Days = Annotated[int, Field(ge=1, le=365, description="Look-back window in days (1–365).")]
Limit = Annotated[int, Field(ge=1, le=500, description="Maximum number of records to return (1–500).")]
# Tool manifest hints. Discovery uses these (authoritative source) to classify each agent→tool edge as a
# read or write path, which decides which faults may be injected on it.
READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, destructive_hint=False, open_world_hint=False)
# Writes alerts, but idempotently (unique per rule+reading), so retrying it is safe.
IDEMPOTENT_WRITE = ToolAnnotations(read_only_hint=False, idempotent_hint=True, destructive_hint=False, open_world_hint=False)

MetricName = Literal["heart_rate", "blood_pressure", "temperature", "spo2", "weight", "sleep"]


def bootstrap(server_name: str) -> logging.Logger:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format=f"%(asctime)s [{server_name}] %(levelname)s %(name)s: %(message)s",
    )
    init_db()
    log = logging.getLogger(server_name)
    log.info("server process starting (stdio transport)")
    return log


def guarded(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Turn anticipated domain errors into ToolError so the client gets a clear is_error result."""
    try:
        return fn(*args, **kwargs)
    except PatientNotFound as exc:
        raise ToolError(str(exc)) from exc
    except ValueError as exc:
        raise ToolError(f"Invalid request: {exc}") from exc
