#!/usr/bin/env sh
# macOS/Linux launcher (extra args are passed through, e.g. ./start.sh --check)
cd "$(dirname "$0")" && exec python3 start.py "$@"
