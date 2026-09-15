#!/bin/bash
# Called by the "Update Tool" button in app.py after a successful git pull.
# Runs detached from the Streamlit process so it survives killing it.
cd "$(dirname "$0")" || exit 1
OLD_PID="$1"

source .venv/bin/activate
pip install -q -r requirements.txt >> update.log 2>&1

sleep 1
if [ -n "$OLD_PID" ]; then
  kill -9 "$OLD_PID" 2>/dev/null
fi

sleep 1
nohup streamlit run app.py --server.headless true --server.port 8501 > /tmp/fund_tool_streamlit.log 2>&1 &
disown
