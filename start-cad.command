#!/bin/sh
# Mac: double-click to start the Interim CAD and open it in your browser. Keep this window open.
cd "$(dirname "$0")"
[ -f config.ini ] || python3 manage.py setup || exit 1
exec python3 manage.py serve --open
