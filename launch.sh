#!/bin/bash
cd "$(dirname "$0")" || exit 1

if ! lsof -ti:8501 >/dev/null 2>&1; then
  source .venv/bin/activate
  nohup streamlit run app.py --server.headless true --server.port 8501 > /tmp/fund_tool_streamlit.log 2>&1 &
  disown
  sleep 3
fi

open "http://localhost:8501"
