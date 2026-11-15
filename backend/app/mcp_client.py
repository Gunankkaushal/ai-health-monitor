"""MCP client manager — the backend's only path to the MCP servers.

* Launches each server as a subprocess and connects over the **stdio** transport using the
  official MCP Python SDK (``mcp.Client`` + ``mcp.client.stdio.stdio_client``).
* Discovers tools with ``tools/list`` and executes them with ``tools/call``.
* Validates arguments against the discovered JSON Schema before sending.
* Applies per-call timeouts, heartbeats each connection, reconnects with backoff,
  and shuts the subprocesses down cleanly (the SDK closes stdin, then kills the tree).
* Server stderr (their logs) is redirected to ``backend/logs/mcp_<key>.log`` so it never
  mixes with the JSON-RPC stream on stdout.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anyio
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as SchemaValidationError

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Implementation

from health_core.settings import BACKEND_DIR, resolve_database_url

log = logging.getLogger("app.mcp")

LOG_DIR = BACKEND_DIR / "logs"


@dataclass(frozen=True)
class ServerSpec:
    key: str
    name: str
    module: str
    description: str


DEFAULT_SERVERS: tuple[ServerSpec, ...] = (
    ServerSpec("patient_data", "Patient Data MCP Server", "mcp_servers.patient_data_server",
               "Profiles, readings, history and trend statistics"),
    ServerSpec("health_rules", "Health Rules MCP Server", "mcp_servers.health_rules_server",
               "Deterministic monitoring rules and alerts"),
    ServerSpec("reports", "Reports MCP Server", "mcp_servers.reports_server",
               "Health summaries and exportable reports"),
)


class MCPClientError(Exception):
    """Base class for client-side MCP failures."""


class MCPUnavailableError(MCPClientError):
    pass


class MCPTimeoutError(MCPClientError):
    pass


class MCPToolError(MCPClientError):
    """The server executed the call and reported is_error=True."""


class ToolArgumentError(MCPClientError):
    pass


class UnknownToolError(MCPClientError):
    pass


@dataclass
class ToolInfo:
    name: str
    description: str
    input_schema: dict[str, Any]
    server: str
    # Manifest hints (MCP ToolAnnotations). None means the server did not declare it.
    read_only: bool | None = None
    idempotent: bool | None = None


@dataclass
class ServerState:
    spec: ServerSpec
    status: str = "stopped"  # starting | connected | error | stopped
    error: str | None = None
    tools: list[ToolInfo] = field(default_factory=list)
    protocol_version: str | None = None
    server_version: str | None = None
    connected_at: str | None = None
    last_heartbeat: str | None = None
    restarts: int = 0
    calls: int = 0
    failures: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.spec.key,
            "name": self.spec.name,
            "description": self.spec.description,
            "transport": "stdio",
            "command": f"python -m {self.spec.module}",
            "status": self.status,
            "error": self.error,
            "protocol_version": self.protocol_version,
            "server_version": self.server_version,
            "connected_at": self.connected_at,
            "last_heartbeat": self.last_heartbeat,
            "restarts": self.restarts,
            "calls": self.calls,
            "failures": self.failures,
            "tools": [{"name": t.name, "description": t.description, "input_schema": t.input_schema,
                       "read_only": t.read_only, "idempotent": t.idempotent} for t in self.tools],
        }


@dataclass
class ToolCallResult:
    server: str
    tool: str
    data: Any
    duration_ms: int


def _tool_info(t: Any, server: str) -> ToolInfo:
    ann = getattr(t, "annotations", None)
    return ToolInfo(t.name, t.description or "", dict(t.input_schema or {}), server,
                    read_only=getattr(ann, "read_only_hint", None) if ann else None,
                    idempotent=getattr(ann, "idempotent_hint", None) if ann else None)


def _root_cause(exc: BaseException) -> BaseException:
    """Unwrap anyio/asyncio ExceptionGroups so status messages show the real error."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MCPManager:
    def __init__(
        self,
        servers: tuple[ServerSpec, ...] = DEFAULT_SERVERS,
        *,
        database_url: str | None = None,
        call_timeout: float = 20.0,
        connect_timeout: float = 45.0,
        heartbeat_interval: float = 15.0,
        max_restarts: int = 5,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self.specs = servers
        self.database_url = resolve_database_url(database_url)
        self.call_timeout = call_timeout
        self.connect_timeout = connect_timeout
        self.heartbeat_interval = heartbeat_interval
        self.max_restarts = max_restarts
        self.extra_env = extra_env or {}
        self.states: dict[str, ServerState] = {s.key: ServerState(s) for s in servers}
        self._clients: dict[str, Client] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._ready: dict[str, asyncio.Event] = {}
        self._restart: dict[str, asyncio.Event] = {}
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------ lifecycle
    def _params(self, spec: ServerSpec) -> StdioServerParameters:
        env = {
            "DATABASE_URL": self.database_url,
            "PYTHONPATH": str(BACKEND_DIR),
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
            **self.extra_env,
        }
        if os.environ.get("RULES_CONFIG_PATH"):
            env.setdefault("RULES_CONFIG_PATH", os.environ["RULES_CONFIG_PATH"])
        # Deliberately NOT forwarding GROQ_API_KEY: the MCP servers never need it.
        return StdioServerParameters(command=sys.executable, args=["-m", spec.module], env=env, cwd=str(BACKEND_DIR))

    async def start(self) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        for spec in self.specs:
            self._ready[spec.key] = asyncio.Event()
            self._restart[spec.key] = asyncio.Event()
            self._tasks[spec.key] = asyncio.create_task(self._supervise(spec), name=f"mcp:{spec.key}")
        try:
            await asyncio.wait_for(
                asyncio.gather(*(e.wait() for e in self._ready.values())), timeout=self.connect_timeout
            )
        except asyncio.TimeoutError:
            log.warning("Not all MCP servers connected within %.0fs", self.connect_timeout)

    async def stop(self) -> None:
        self._stop.set()
        for ev in self._restart.values():
            ev.set()
        tasks = list(self._tasks.values())
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=15)
            for t in pending:
                t.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()
        for st in self.states.values():
            st.status = "stopped"
        log.info("MCP servers stopped")

    async def reconnect(self, key: str | None = None) -> None:
        keys = [key] if key else list(self.states)
        for k in keys:
            if k not in self.states:
                raise MCPClientError(f"Unknown server '{k}'")
            st = self.states[k]
            st.restarts = 0
            st.status, st.error = "starting", None  # not "connected" until the new session is up
            self._clients.pop(k, None)
            self._ready[k].clear()
            task = self._tasks.get(k)
            if task is None or task.done():
                self._tasks[k] = asyncio.create_task(self._supervise(st.spec), name=f"mcp:{k}")
            else:
                self._restart[k].set()
        await asyncio.sleep(0.2)
        deadline = time.monotonic() + self.connect_timeout
        while time.monotonic() < deadline:
            if all(self.states[k].status == "connected" or self._tasks[k].done() for k in keys):
                break
            await asyncio.sleep(0.2)

    async def _supervise(self, spec: ServerSpec) -> None:
        state = self.states[spec.key]
        backoff = 1.0
        while not self._stop.is_set():
            state.status, state.error = "starting", None
            try:
                await self._connect_and_serve(spec, state)
                backoff = 1.0
            except Exception as exc:  # noqa: BLE001 - we report every failure in the status panel
                root = _root_cause(exc)
                state.status = "error"
                state.error = f"{type(root).__name__}: {root}" if str(root) else type(root).__name__
                log.error("MCP server %s failed: %s", spec.key, state.error)
            finally:
                self._clients.pop(spec.key, None)
                self._ready[spec.key].set()  # unblock start() even on failure
            if self._stop.is_set():
                break
            state.restarts += 1
            if state.restarts > self.max_restarts:
                state.status = "error"
                state.error = (state.error or "") + " (giving up after repeated failures; use Reconnect)"
                break
            self._restart[spec.key].clear()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 30)
            self._ready[spec.key].clear()

    async def _connect_and_serve(self, spec: ServerSpec, state: ServerState) -> None:
        log_path = LOG_DIR / f"mcp_{spec.key}.log"
        with open(log_path, "a", encoding="utf-8") as errlog:
            errlog.write(f"\n===== {_now()} starting {spec.module} =====\n")
            errlog.flush()
            transport = stdio_client(self._params(spec), errlog=errlog)
            # One properly nested cancel scope: its deadline bounds the connect + discovery
            # phase and is lifted once the server is up.
            with anyio.CancelScope(deadline=anyio.current_time() + self.connect_timeout) as connect_scope:
                async with Client(
                    transport,
                    read_timeout_seconds=self.call_timeout,
                    client_info=Implementation(name="health-monitor-backend", version="1.0.0"),
                ) as client:
                    try:
                        await self._serve(spec, state, client, connect_scope)
                    finally:
                        self._clients.pop(spec.key, None)
            if connect_scope.cancelled_caught:
                raise TimeoutError(f"connect/discovery did not finish within {self.connect_timeout:.0f}s")
            if self._stop.is_set():
                state.status = "stopped"

    async def _serve(self, spec: ServerSpec, state: ServerState, client: Client, connect_scope: anyio.CancelScope) -> None:
        """Discover tools, publish the connection, then heartbeat until stop/restart."""
        listing = await client.list_tools()
        connect_scope.deadline = float("inf")  # connected: lift the connect deadline
        state.tools = [_tool_info(t, spec.key) for t in listing.tools]
        state.protocol_version = client.protocol_version
        info = client.server_info
        state.server_version = getattr(info, "version", None) if info else None
        state.status, state.error = "connected", None
        state.connected_at = state.last_heartbeat = _now()
        self._clients[spec.key] = client
        self._ready[spec.key].set()
        log.info("MCP %s connected (%s); tools: %s", spec.key, state.protocol_version,
                 ", ".join(t.name for t in state.tools))

        # Heartbeat. `ping` was removed in protocol 2026-07-28, so liveness is checked with a real
        # tools/list round-trip (bypassing the client cache), which also refreshes discovery.
        # A failure raises, ending this connection so the supervisor reconnects.
        while not self._stop.is_set() and not self._restart[spec.key].is_set():
            try:
                await asyncio.wait_for(self._restart[spec.key].wait(), timeout=self.heartbeat_interval)
            except asyncio.TimeoutError:
                pass
            if self._stop.is_set() or self._restart[spec.key].is_set():
                break
            with anyio.fail_after(10):
                listing = await client.list_tools(cache_mode="refresh")
            state.tools = [_tool_info(t, spec.key) for t in listing.tools]
            state.last_heartbeat = _now()

    # ------------------------------------------------------------------ discovery
    def status(self) -> list[dict[str, Any]]:
        return [s.as_dict() for s in self.states.values()]

    def all_tools(self) -> dict[str, ToolInfo]:
        return {t.name: t for st in self.states.values() if st.status == "connected" for t in st.tools}

    def tool_info(self, name: str) -> ToolInfo:
        for st in self.states.values():
            for t in st.tools:
                if t.name == name:
                    return t
        raise UnknownToolError(f"Tool '{name}' was not discovered on any MCP server")

    # ------------------------------------------------------------------ execution
    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None, *, timeout: float | None = None) -> ToolCallResult:
        info = self.tool_info(name)
        state = self.states[info.server]
        client = self._clients.get(info.server)
        if client is None or state.status != "connected":
            raise MCPUnavailableError(f"{state.spec.name} is not connected ({state.status})")

        args = dict(arguments or {})
        try:
            Draft202012Validator(info.input_schema).validate(args)
        except SchemaValidationError as exc:
            where = "/".join(str(p) for p in exc.absolute_path) or "(root)"
            raise ToolArgumentError(f"Invalid arguments for {name} at {where}: {exc.message}") from None

        timeout = timeout or self.call_timeout
        state.calls += 1
        started = time.perf_counter()
        try:
            with anyio.fail_after(timeout):
                result = await client.call_tool(name, args, read_timeout_seconds=timeout)
        except TimeoutError:
            state.failures += 1
            raise MCPTimeoutError(f"{name} timed out after {timeout:.0f}s") from None
        except MCPClientError:
            raise
        except Exception as exc:  # transport/protocol failure → trigger reconnect
            state.failures += 1
            state.status, state.error = "error", f"{type(exc).__name__}: {exc}"
            self._restart[info.server].set()
            raise MCPUnavailableError(f"{name} failed at the transport level: {exc}") from exc
        duration = int((time.perf_counter() - started) * 1000)

        text = "\n".join(getattr(c, "text", "") for c in result.content if getattr(c, "type", "") == "text")
        if result.is_error:
            state.failures += 1
            raise MCPToolError(text or f"{name} reported an error")

        data: Any
        try:
            data = json.loads(text) if text else None
        except json.JSONDecodeError:
            data = text
        if data is None and result.structured_content is not None:
            data = result.structured_content
        return ToolCallResult(server=info.server, tool=name, data=data, duration_ms=duration)
