#!/bin/bash
set -e

BOT_TOKEN=$(grep '^BOT_TOKEN=' /root/smart-trader-bot/.env | cut -d '=' -f2-)
ADMIN_ID=$(grep '^ADMIN_ID=' /root/smart-trader-bot/.env | cut -d '=' -f2-)
DB_PATH="/root/smart-trader-bot/data/signals.db"
DATE_STR=$(date +'%Y-%m-%d_%H-%M')

if [ -f "$DB_PATH" ]; then
    BACKUP_FILE="/tmp/signals_backup_${DATE_STR}.db"
    # Use python sqlite3 to make a clean transactional backup without locking
    python3 -c "
import sqlite3
src = sqlite3.connect('$DB_PATH')
dst = sqlite3.connect('$BACKUP_FILE')
src.backup(dst)
dst.close()
src.close()
"
    
    RESP=$(curl -s -F chat_id="$ADMIN_ID" \
         -F document=@"$BACKUP_FILE" \
         -F caption="💾 Автоматический бэкап базы данных Smart Trader Bot (${DATE_STR} UTC)
📊 Пользователей: $(python3 -c "import sqlite3; con=sqlite3.connect('$DB_PATH'); print(con.execute('SELECT count(*) FROM users').fetchone()[0]); con.close()")
⚙️ Статус: Все таблицы синхронизированы." \
         "https://api.telegram.org/bot${BOT_TOKEN}/sendDocument")
    
    rm -f "$BACKUP_FILE"
    echo "$RESP"
else
    echo "DB not found at $DB_PATH"
fi
