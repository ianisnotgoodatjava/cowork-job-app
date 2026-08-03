#!/bin/bash
# Wrapper for the scheduled run. launchd starts with a bare environment, so
# everything here is absolute and the working directory is set explicitly.
cd "/ABSOLUTE/PATH/TO/THIS/PROJECT" || exit 1

mkdir -p logs
LOG="logs/auto-$(date +%Y-%m-%d).log"

{
  echo "===== run at $(date) ====="
  ./venv/bin/python cli.py auto --days 30
  echo
} >> "$LOG" 2>&1

# Keep the last 30 daily logs.
ls -1t logs/auto-*.log 2>/dev/null | tail -n +31 | xargs -I {} rm -f {}
