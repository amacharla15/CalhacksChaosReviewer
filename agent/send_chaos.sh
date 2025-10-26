#!/usr/bin/env bash
set -euo pipefail
TAIL=".vscode/chaos_tail.log"
LANG="${1:-cpp}"

# Read last 200 lines and escape for JSON (quotes + backslashes)
if [[ -f "$TAIL" ]]; then
  TAIL_TEXT="$(tail -n 200 "$TAIL" | sed 's/\\/\\\\/g; s/"/\\"/g')"
else
  TAIL_TEXT=""
fi

JSON="{\"context\":{\"lang\":\"$LANG\"},\"log_tail\":\"$TAIL_TEXT\"}"
curl -sS -X POST http://localhost:8787/chaos \
  -H 'Content-Type: application/json' \
  -d "$JSON"
