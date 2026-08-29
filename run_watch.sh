#!/bin/bash
# Fired by launchd at 06:00, 18:00 and 23:00 local (Eastern) time. Absolute
# paths throughout: launchd starts with a bare environment and no working
# directory.
cd "/ABSOLUTE/PATH/TO/THIS/PROJECT" || exit 1
mkdir -p logs
LOG="logs/watch-$(date +%Y-%m-%d).log"
{
  echo "===== $(date) ====="
  # --limit 8: each application researched gets up to three contacts and
  # three drafts, so this is up to 24 drafted emails per pass. The 23:00 run
  # additionally does the daily maintenance sweeps (watch decides by hour).
  ./venv/bin/python -u cli.py watch --days 14 --limit 8
  echo
} >> "$LOG" 2>&1
# Keep a fortnight of logs.
ls -1t logs/watch-*.log 2>/dev/null | tail -n +15 | xargs -I {} rm -f {}
