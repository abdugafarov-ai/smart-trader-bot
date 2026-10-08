#!/bin/bash
# Smart Trader 24/7 Watchdog

if ! systemctl is-active --quiet smart-trader-bot.service; then
    echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') - smart-trader-bot is inactive. Restarting..." >> /root/smart-trader-bot/watchdog.log
    systemctl restart smart-trader-bot.service
fi

# MT5 Terminal Self-Healing
if ! pgrep -f "terminal64" > /dev/null; then
    echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') - MT5 terminal64 is offline. Launching start_mt5.sh..." >> /root/smart-trader-bot/watchdog.log
    su - trader -c "/home/trader/start_mt5.sh" > /dev/null 2>&1 &
fi

FREE_MB=$(df -m / | awk 'NR==2 {print $4}')
if [ $FREE_MB -lt 300 ]; then
    echo Low disk space ${FREE_MB} MB. Cleaning apt and logs... >> /root/smart-trader-bot/watchdog.log
    apt-get clean
    find /var/log -type f -name *.log -size +50M -exec truncate -s 10M {} +
    if [ -f /root/smart-trader-bot/bot.log ] && [ $(stat -c%s /root/smart-trader-bot/bot.log) -gt 50000000 ]; then
        tail -n 10000 /root/smart-trader-bot/bot.log > /root/smart-trader-bot/bot.log.tmp && mv /root/smart-trader-bot/bot.log.tmp /root/smart-trader-bot/bot.log
    fi
fi

exit 0
