#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
DIR=$(pwd)
command -v apt-get >/dev/null && sudo apt-get install -y -qq python3-venv
id flightbot >/dev/null 2>&1 || sudo useradd --system --no-create-home --shell /usr/sbin/nologin flightbot
python3 -m venv venv
venv/bin/pip install -q -r requirements.txt
if ! sudo test -f /etc/flightbot.env; then
    sudo install -m 600 deploy/flightbot.env.example /etc/flightbot.env
    echo "Created /etc/flightbot.env. Put your TELEGRAM_BOT_TOKEN in it (sudo nano /etc/flightbot.env), then rerun $0"
    exit 1
fi
sed "s#/opt/flightbot#$DIR#g" deploy/flightbot.service | sudo tee /etc/systemd/system/flightbot.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable flightbot
sudo systemctl restart flightbot
sleep 3
systemctl --no-pager status flightbot | head -5
journalctl -u flightbot -n 5 --no-pager
