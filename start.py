#!/usr/bin/env python3
"""One-command launcher for the AI Health Monitoring System.

    python start.py              # set up if needed, then run backend + frontend
    python start.py --check      # run all tests + frontend typecheck/build, then exit
    python start.py --reset-db   # wipe and reseed the synthetic database before starting
    python start.py --setup-only # just install dependencies

Processes managed:
  * backend  - FastAPI/uvicorn on http://127.0.0.1:8000 (it launches the 3 MCP servers over stdio itself)
  * frontend - Vite dev server on http://localhost:5173
Ctrl+C stops everything (backend shuts its MCP subprocesses down cleanly).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
VENV = BACKEND / ".venv"
WIN = sys.platform == "win32"
VENV_PY = VENV / ("Scripts/python.exe" if WIN else "bin/python")
NPM = "npm.cmd" if WIN else "npm"


def say(msg: str) -> None:
    print(f"[start] {msg}", flush=True)


def run(cmd: list[str], cwd: Path) -> None:
    say(" ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def ensure_env_file() -> None:
    env = ROOT / ".env"
    if not env.exists():
        shutil.copy(ROOT / ".env.example", env)
        say("Created .env from .env.example - add your GROQ_API_KEY there later (optional).")


def ensure_backend() -> None:
    if sys.version_info < (3, 11):
        sys.exit("Python 3.11+ is required (3.12 recommended).")
    if not VENV_PY.exists():
        say("Creating Python virtual environment...")
        run([sys.executable, "-m", "venv", str(VENV)], ROOT)
    req = BACKEND / "requirements.txt"
    stamp = VENV / ".requirements.sha256"
    digest = hashlib.sha256(req.read_bytes()).hexdigest()
    if not stamp.exists() or stamp.read_text().strip() != digest:
        say("Installing backend dependencies...")
        run([str(VENV_PY), "-m", "pip", "install", "--upgrade", "pip", "-q"], BACKEND)
        run([str(VENV_PY), "-m", "pip", "install", "-r", str(req), "-q"], BACKEND)
        stamp.write_text(digest)


def ensure_frontend() -> None:
    if shutil.which(NPM) is None:
        sys.exit("Node.js/npm not found. Install Node.js 20+ (https://nodejs.org) and re-run.")
    if not (FRONTEND / "node_modules").exists():
        say("Installing frontend dependencies...")
        run([NPM, "install", "--no-fund", "--no-audit"], FRONTEND)


def spawn(cmd: list[str], cwd: Path) -> subprocess.Popen:
    kwargs: dict = {"cwd": cwd}
    if WIN:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


def stop(proc: subprocess.Popen, name: str) -> None:
    if proc.poll() is not None:
        return
    say(f"Stopping {name}...")
    try:
        if WIN:
            proc.send_signal(signal.CTRL_BREAK_EVENT)  # uvicorn/vite handle this as a graceful shutdown
        else:
            os.killpg(proc.pid, signal.SIGINT)
        proc.wait(timeout=15)
    except Exception:
        if WIN:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        else:
            os.killpg(proc.pid, signal.SIGKILL)


def wait_for(url: str, proc: subprocess.Popen, timeout: float = 90) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def check() -> int:
    ensure_backend()
    ensure_frontend()
    say("Running backend tests (real MCP servers, demo mode, isolated temp DB)...")
    rc = subprocess.run([str(VENV_PY), "-m", "pytest", "-q"], cwd=BACKEND).returncode
    say("Type-checking and building frontend...")
    rc2 = subprocess.run([NPM, "run", "build"], cwd=FRONTEND).returncode
    say("ALL CHECKS PASSED" if rc == rc2 == 0 else "SOME CHECKS FAILED")
    return rc or rc2


def _interrupt(*_):
    raise KeyboardInterrupt


def main() -> int:
    if WIN:  # treat Ctrl+Break like Ctrl+C so the cleanup below always runs
        signal.signal(signal.SIGBREAK, _interrupt)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="run tests and frontend build, then exit")
    ap.add_argument("--setup-only", action="store_true", help="install dependencies and exit")
    ap.add_argument("--reset-db", action="store_true", help="wipe and reseed the synthetic database")
    ap.add_argument("--backend-only", action="store_true", help="do not start the frontend dev server")
    args = ap.parse_args()

    ensure_env_file()
    if args.check:
        return check()
    ensure_backend()
    if not args.backend_only:
        ensure_frontend()
    if args.setup_only:
        say("Setup complete.")
        return 0
    if args.reset_db:
        run([str(VENV_PY), "-m", "app.seed", "--reset"], BACKEND)

    procs: list[tuple[str, subprocess.Popen]] = []
    try:
        backend = spawn([str(VENV_PY), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"], BACKEND)
        procs.append(("backend", backend))
        say("Waiting for backend (it starts and connects the MCP servers)...")
        if not wait_for("http://127.0.0.1:8000/api/health", backend):
            say("Backend failed to start - see output above and backend/logs/.")
            return 1
        say("Backend ready: http://127.0.0.1:8000  (API docs: /docs)")
        if not args.backend_only:
            procs.append(("frontend", spawn([NPM, "run", "dev", "--", "--host", "localhost"], FRONTEND)))
            say("Dashboard: http://localhost:5173")
        say("Press Ctrl+C to stop all processes.")
        while all(p.poll() is None for _, p in procs):
            time.sleep(1)
        for name, p in procs:
            if p.poll() is not None:
                say(f"{name} exited with code {p.returncode}")
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        for name, p in reversed(procs):
            stop(p, name)
        say("All processes stopped.")


if __name__ == "__main__":
    sys.exit(main())
