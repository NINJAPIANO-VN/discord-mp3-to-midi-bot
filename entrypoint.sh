#!/bin/bash
set -e

# Start the Discord bot with auto-restart in the background
(
  while true; do
    python /app/bot.py
    echo "[entrypoint] Bot exited, restarting in 5s..."
    sleep 5
  done
) &

# Start the status server in the foreground (keeps container alive)
exec python /app/status_server.py
