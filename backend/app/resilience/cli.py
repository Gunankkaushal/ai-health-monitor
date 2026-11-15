"""CI gate: run a resilience suite against a running backend and exit non-zero if the gate blocks.

    python -m app.resilience.cli list
    python -m app.resilience.cli run-suite --suite 1 [--wait-for-window] [--fail-below 70]

Exit codes: 0 gate passed (or warned / report-only), 1 gate blocked, 2 error or timeout.
Uses only the standard library, so any CI runner with Python can call it. The pipeline step holds
while a blackout window is active (--wait-for-window) instead of failing.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any


def _call(base: str, actor: str, method: str, path: str, body: Any = None) -> Any:
    req = urllib.request.Request(base.rstrip("/") + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "X-Actor": actor})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"[resilience] {method} {path} failed: HTTP {exc.code} {exc.read().decode(errors='replace')}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--actor", default="ci-pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list suites and their last gate result")
    rs = sub.add_parser("run-suite", help="run a suite and gate on the journey score")
    rs.add_argument("--suite", type=int, required=True)
    rs.add_argument("--wait-for-window", action="store_true", help="hold during blackout windows instead of failing")
    rs.add_argument("--fail-below", type=int, default=None, help="override the suite's own gate threshold")
    rs.add_argument("--timeout", type=int, default=1800, help="seconds to wait for the suite to finish")
    a = ap.parse_args(argv)

    try:
        if a.cmd == "list":
            for s in _call(a.base_url, a.actor, "GET", "/api/resilience/suites"):
                last = s["last_run"] or {}
                print(f"{s['id']:>3}  {s['name']:<45} {s['journey_id']:<10} gate {s['gate_mode']} <{s['gate_threshold']}  "
                      f"last: {last.get('gate') or '-'} {last.get('score') if last else ''}")
            return 0

        sr = _call(a.base_url, a.actor, "POST", f"/api/resilience/suites/{a.suite}/run", {"wait_for_window": a.wait_for_window})
        print(f"[resilience] suite run {sr['id']} started by {a.actor}")
        deadline, last_status = time.time() + a.timeout, None
        while time.time() < deadline:
            sr = _call(a.base_url, a.actor, "GET", f"/api/resilience/suite-runs/{sr['id']}")
            if sr["status"] != last_status:
                print(f"[resilience] {sr['status']}" + (f" — {sr['detail']}" if sr["detail"] else ""))
                last_status = sr["status"]
            if sr["status"] in ("completed", "failed"):
                break
            time.sleep(2)
        else:
            print("[resilience] timed out waiting for the suite")
            return 2
        for r in sr.get("runs", []):
            print(f"  run-{r['id']:04d}  {r['status']:<11} {r['verdict'] or '-':<14} score {r['score'] if r['score'] is not None else '-':<4} {r['name']}")
        if sr["status"] == "failed":
            print(f"[resilience] suite failed: {sr['detail']}")
            return 2
        gate = sr["gate"]
        if a.fail_below is not None:
            gate = "blocked" if (sr["score"] or 0) < a.fail_below else "passed"
        print(f"[resilience] journey score {sr['score']} → gate {gate.upper()}")
        return 1 if gate == "blocked" else 0
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"[resilience] cannot reach {a.base_url}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
