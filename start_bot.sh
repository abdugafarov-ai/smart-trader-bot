#!/bin/bash
cd /root/smart-trader-bot
exec /root/smart-trader-bot/venv/bin/python3 main.py >> /root/smart-trader-bot/bot.log 2>&1
