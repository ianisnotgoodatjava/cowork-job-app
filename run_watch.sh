#!/bin/bash
# Fired every 3 hours by launchd. Absolute paths throughout: launchd starts
# with a bare environment and no working directory.
cd "/ABSOLUTE/PATH/TO/THIS/PROJECT" || exit 1
mkdir -p logs
LOG="logs/watch-$(date +%Y-%m-%d).log"
{
  echo "===== $(date) ====="
  ./venv/bin/python -u cli.py watch --days 14 --limit 3
  echo
} >> "$LOG" 2>&1
# Keep a fortnight of logs; a 3-hourly job fills a directory quickly.
ls -1t logs/watch-*.log 2>/dev/null | tail -n +15 | xargs -I {} rm -f {}
