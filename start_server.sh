#!/bin/sh
cd "$(dirname "$0")"
(sleep 1; (command -v open >/dev/null && open http://localhost:8000) || (command -v xdg-open >/dev/null && xdg-open http://localhost:8000)) >/dev/null 2>&1 &
exec python3 server.py "$@"
