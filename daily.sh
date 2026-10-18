#!/bin/zsh
# Run by launchd every morning: new emails first, then follow-ups that are due.
# caffeinate keeps the Mac awake until the batch finishes.
cd "$(dirname "$0")" || exit 1
PY=/opt/homebrew/bin/python3

echo "===== $(date) ====="
/usr/bin/caffeinate -i "$PY" send.py --send
/usr/bin/caffeinate -i "$PY" send.py --followup --send
echo
