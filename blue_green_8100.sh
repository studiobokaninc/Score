#!/bin/bash
# 案B: ブルーグリーン即応スクリプト
# 前提: 旧プロセス(pid 1961275, :8100)が外部(人手/殿)によって停止済みであること
# 実行者: 家老(karo) — 殿裁可・将軍合図後のみ実行

set -e

TARGET_PID=1961275
TARGET_PORT=8100
SCORE_BE_DIR="/mnt/h/multi-agent-shogun-main/score_be"
LOG_FILE="/tmp/uvicorn_8100_new.log"

echo "=== ブルーグリーン起動スクリプト (案B) ==="
echo "実行日時: $(date '+%Y-%m-%d %H:%M:%S')"

# 1. 旧プロセス停止確認
echo ""
echo "■ STEP 1: 旧プロセス停止確認"
if ps -p "$TARGET_PID" > /dev/null 2>&1; then
    echo "ERROR: pid $TARGET_PID はまだ稼働中。外部停止完了後に再実行せよ。"
    ps -p "$TARGET_PID" -o pid,cmd
    exit 1
fi
echo "OK: pid $TARGET_PID は停止済。"

# 2. ポート空き確認
echo ""
echo "■ STEP 2: port $TARGET_PORT 空き確認"
if ss -tlnp | grep -q ":$TARGET_PORT "; then
    echo "ERROR: port $TARGET_PORT はまだ使用中。"
    ss -tlnp | grep ":$TARGET_PORT "
    exit 1
fi
echo "OK: port $TARGET_PORT は空き。"

# 3. 修正版起動
echo ""
echo "■ STEP 3: 修正版(Score-BE with .env) を port $TARGET_PORT で起動"
cd "$SCORE_BE_DIR"
nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "$TARGET_PORT" > "$LOG_FILE" 2>&1 &
NEW_PID=$!
echo "起動 PID: $NEW_PID"

# 4. ヘルスチェック
echo ""
echo "■ STEP 4: ヘルスチェック待機"
sleep 4
HEALTH=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$TARGET_PORT/api/health" 2>/dev/null || echo "000")
if [ "$HEALTH" != "200" ]; then
    echo "ERROR: /api/health = $HEALTH (期待値: 200)"
    echo "ログ: $LOG_FILE"
    cat "$LOG_FILE" | tail -20
    exit 1
fi
echo "OK: /api/health = $HEALTH"
echo ""
echo "=== 起動完了 ==="
echo "新プロセス PID: $NEW_PID"
echo "URL: http://localhost:$TARGET_PORT"
echo "ログ: $LOG_FILE"
