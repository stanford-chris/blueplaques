#!/bin/bash
# One-off: post and pin the about-this-account thread, then install and load
# the twice-daily posting job, whose first slot (18:20 KST) posts the first
# plaque. Fired once by com.chrisstanford.blueplaques-launch at 18:18 KST on
# 6 October 2026.
#
# If the thread fails, this exits 1 and leaves its own job in place as the
# signal, and the posting job is NOT loaded: no plaque goes out without the
# pinned thread above it.
#
# Order at the end is load-bearing (reference_launchd_self_removing_job):
# delete this job's plist first, boot it out last, because bootout ends the
# running script.
set -u
REPO="$HOME/Projects/blueplaques"
PY=/Library/Frameworks/Python.framework/Versions/3.13/bin/python3
AGENTS="$HOME/Library/LaunchAgents"
SELF=com.chrisstanford.blueplaques-launch
JOB=com.chrisstanford.blueplaques

echo "===== $(date '+%Y-%m-%d %H:%M:%S') launch start ====="
cd "$REPO" || exit 1
if ! "$PY" blueplaques_pin.py --post; then
    echo "!! pinned thread FAILED: posting job not loaded"
    exit 1
fi
cp "$REPO/data/$JOB.plist" "$AGENTS/$JOB.plist" || exit 1
launchctl bootstrap "gui/$UID" "$AGENTS/$JOB.plist" || { echo "!! bootstrap of $JOB FAILED"; exit 1; }
launchctl list | grep "$JOB\$" || { echo "!! $JOB not listed after bootstrap"; exit 1; }
echo "posting job loaded; first slot 18:20 KST"
rm -f "$AGENTS/$SELF.plist" && echo "removed own plist"
echo "===== $(date '+%Y-%m-%d %H:%M:%S') launch done ====="
launchctl bootout "gui/$UID/$SELF"
