#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR="${1:-$(pwd)}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

mkdir -p "$TARGET_DIR/logs"

TMP_CRON="$(mktemp)"
crontab -l 2>/dev/null | grep -v 'market_push.*run_stock_shape.py' | grep -v 'market_push.*run_news_radar.py' | grep -v 'market_push.*run_morning_brief.py' > "$TMP_CRON" || true

cat >> "$TMP_CRON" <<EOF
0 8 * * * cd "$TARGET_DIR" && /usr/bin/env bash -lc 'set -a; . ./.env; set +a; $PYTHON_BIN run_morning_brief.py' >> "$TARGET_DIR/logs/morning_brief.log" 2>&1
45 15 * * 1-5 cd "$TARGET_DIR" && /usr/bin/env bash -lc 'set -a; . ./.env; set +a; $PYTHON_BIN run_stock_shape.py' >> "$TARGET_DIR/logs/stock_shape.log" 2>&1
30 22 * * * cd "$TARGET_DIR" && /usr/bin/env bash -lc 'set -a; . ./.env; set +a; $PYTHON_BIN run_news_radar.py' >> "$TARGET_DIR/logs/news_radar.log" 2>&1
EOF

crontab "$TMP_CRON"
rm -f "$TMP_CRON"

echo "Installed market_push cron jobs for $TARGET_DIR"
