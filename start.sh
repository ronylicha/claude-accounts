#!/usr/bin/env bash
# Claude Accounts Manager — Quick Start
# Builds and starts the Docker container, then opens the browser.
set -e

cd "$(dirname "$0")"

echo "Building and starting Claude Accounts Manager..."
docker compose up -d --build

echo "Waiting for server..."
for i in $(seq 1 15); do
  if curl -sf http://localhost:5111/api/auth/status >/dev/null 2>&1; then
    echo "Server ready!"
    break
  fi
  sleep 1
done

# Open browser (Linux/macOS)
URL="http://localhost:5111"
if command -v xdg-open &>/dev/null; then
  xdg-open "$URL"
elif command -v open &>/dev/null; then
  open "$URL"
else
  echo "Open $URL in your browser"
fi

echo "Claude Accounts Manager running at $URL"
echo "Logs: docker compose logs -f"
echo "Stop: docker compose down"
