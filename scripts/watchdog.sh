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
if [ $FREE_MB -lt 1000 ]; then
    echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') - Low disk space ${FREE_MB} MB (< 1000 MB). Running deep cleanup..." >> /root/smart-trader-bot/watchdog.log
    apt-get clean 2>/dev/null || true
    rm -rf /var/lib/snapd/cache/* 2>/dev/null || true
    journalctl --vacuum-time=2d --vacuum-size=15M 2>/dev/null || true
    rm -f /var/log/*.gz /var/log/*.[0-9] /var/log/*.old 2>/dev/null || true
    rm -f /home/trader/*.png /root/*.png /home/trader/mt5setup.exe /home/trader/Desktop/mt5setup.exe 2>/dev/null || true
    rm -rf "/home/trader/.wine/drive_c/Program Files (x86)/Microsoft/EdgeCore" 2>/dev/null || true
    rm -rf "/home/trader/.wine/drive_c/Program Files (x86)/Microsoft/EdgeWebView" 2>/dev/null || true
    rm -rf /home/trader/.wine/drive_c/users/trader/Temp/* 2>/dev/null || true
    if [ -f /root/smart-trader-bot/bot.log ] && [ $(stat -c%s /root/smart-trader-bot/bot.log) -gt 30000000 ]; then
        tail -n 10000 /root/smart-trader-bot/bot.log > /root/smart-trader-bot/bot.log.tmp && mv /root/smart-trader-bot/bot.log.tmp /root/smart-trader-bot/bot.log
    fi
fi

exit 0
