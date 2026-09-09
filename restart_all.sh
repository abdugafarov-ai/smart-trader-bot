#!/bin/bash
pkill -9 -f terminal64.exe
pkill -9 -f wineserver
sleep 3
su - trader -c 'WINEPREFIX=/home/trader/.wine DISPLAY=:0 nohup wine "/home/trader/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe" > /tmp/mt5.log 2>&1 &'
sleep 5
echo "=== MT5 NEW PROCESS ==="
ps aux | grep terminal64 | grep -v grep
