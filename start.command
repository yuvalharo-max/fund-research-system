#!/bin/bash
# Double-click launcher: starts the app, auto-detects whatever port it actually
# bound to (Streamlit picks a different one if 8501 is busy), opens it in the
# browser, and keeps this Terminal window tied to the server — closing the
# window (or Ctrl+C) stops it.
cd "$(dirname "$0")" || exit 1

LOG_FILE="$(mktemp -t fund_tool_server_log)"
source .venv/bin/activate

streamlit run app.py --server.headless true > "$LOG_FILE" 2>&1 &
SERVER_PID=$!

# Bash doesn't reliably forward SIGHUP/SIGTERM to background jobs on its own —
# closing the Terminal window (or Ctrl+C) needs an explicit trap to actually
# take the server down with it, instead of leaving it orphaned in the background.
cleanup() {
  if kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null
  fi
}
trap cleanup EXIT INT TERM HUP

echo "Starting server (PID $SERVER_PID)... log: $LOG_FILE"

URL=""
for i in $(seq 1 30); do
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "Server process exited early. Log contents:"
    cat "$LOG_FILE"
    exit 1
  fi
  URL=$(grep -oE 'http://localhost:[0-9]+' "$LOG_FILE" | head -n1)
  if [ -n "$URL" ]; then
    break
  fi
  sleep 1
done

if [ -z "$URL" ]; then
  echo "Server didn't report a URL within 30s. Log contents:"
  cat "$LOG_FILE"
  wait "$SERVER_PID"
  exit 1
fi

echo "Server is up at $URL — opening browser..."
open "$URL"

echo ""
echo "Server is running (PID $SERVER_PID). Close this window or press Ctrl+C to stop it."
wait "$SERVER_PID"
