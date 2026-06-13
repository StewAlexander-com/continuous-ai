#!/usr/bin/env bash
# Double-click this in Finder to launch a Continuous-AI chat session.
# Runs from the project folder it lives in; keeps the window open at the end
# so you can read the session summary.
cd "$(dirname "$0")"
bash run.sh chat
echo
echo "------------------------------------------------------------"
echo "[Seedling session ended — review the summary above.]"
read -r -p "Press Return to close this window..."
