#!/bin/bash
# One scheduled run, three times a day (06:00 / 18:00 / 23:00).
#
# This used to be two launchd jobs with two schedules, which read as
# duplicates and behaved worse than one: the pipeline that actually keeps
# the sheet current -- new applications, rejections, drafts -- ran once at
# 03:15, while the job-posting search ran three times. So a rejection that
# arrived at 09:00 sat undetected for eighteen hours, and outreach kept
# being drafted to desks that had already said no.
#
# Both now run in the same slot, pipeline first: `watch` proposes new roles
# to apply to, and it should be reading a sheet that has already had the
# morning's applications and rejections folded in.
cd "/ABSOLUTE/PATH/TO/THIS/PROJECT" || exit 1
mkdir -p logs
LOG="logs/auto-$(date +%Y-%m-%d).log"
{
  echo "===== run at $(date) ====="
  echo "--- pipeline ---"
  ./venv/bin/python -u cli.py auto --days 30
  echo
  echo "--- new postings ---"
  ./venv/bin/python -u cli.py watch --days 14 --limit 8
  echo
} >> "$LOG" 2>&1
ls -1t logs/auto-*.log 2>/dev/null | tail -n +31 | xargs -I {} rm -f {}
