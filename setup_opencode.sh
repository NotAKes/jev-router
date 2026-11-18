#!/usr/bin/env bash
# macOS/Linux launcher for the same installer used on Windows.
set -euo pipefail

if ! command -v python3 >/dev/null 2>&1; then
    echo 'Python 3.10 or newer is required.' >&2
    exit 1
fi

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec python3 "$root/setup_opencode_jev.py" "$@"
