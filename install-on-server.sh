#!/bin/bash
# Ставит ТОЛЬКО бот рун в /opt/rune.
# Не трогает /opt/mozgionline, /opt/simplecourse, /root/whatsapp-api.
set -euo pipefail

RUNE_DIR="/opt/rune"
REPO="https://github.com/Bochok100/rune.git"

if [ "$(id -u)" -ne 0 ]; then
  echo "Запустите от root: sudo bash $0"
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y git python3 python3-venv python3-pip redis-server

# Redis уже может использоваться другими проектами — только включаем, не перезапускаем.
systemctl enable redis-server >/dev/null 2>&1 || systemctl enable redis >/dev/null 2>&1 || true
systemctl start redis-server >/dev/null 2>&1 || systemctl start redis >/dev/null 2>&1 || true

mkdir -p /opt
if [ ! -d "$RUNE_DIR/.git" ]; then
  git clone "$REPO" "$RUNE_DIR"
else
  git -C "$RUNE_DIR" fetch origin
  git -C "$RUNE_DIR" pull --ff-only origin main || true
fi

python3 -m venv "$RUNE_DIR/venv"
"$RUNE_DIR/venv/bin/pip" install --upgrade pip
"$RUNE_DIR/venv/bin/pip" install -r "$RUNE_DIR/requirements.txt"

if [ ! -f "$RUNE_DIR/.env" ]; then
  if [ -f "$RUNE_DIR/.env.example" ]; then
    cp "$RUNE_DIR/.env.example" "$RUNE_DIR/.env"
  else
    cat > "$RUNE_DIR/.env" << 'EOF'
BOT_TOKEN=
PAYMENT_TOKEN=
ADMIN_ID=297967650
REDIS_HOST=localhost
REDIS_PORT=6379
EOF
  fi
  echo "Создан $RUNE_DIR/.env — впишите BOT_TOKEN и PAYMENT_TOKEN."
fi

cat > /etc/systemd/system/rune-bot.service << EOF
[Unit]
Description=Rune Telegram bot
After=network.target redis-server.service redis.service

[Service]
Type=simple
WorkingDirectory=$RUNE_DIR
ExecStart=$RUNE_DIR/venv/bin/python $RUNE_DIR/botrunes.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable rune-bot.service

echo
echo "Готово. Папка бота: $RUNE_DIR"
echo "Другие проекты не менялись."
echo
echo "1) nano $RUNE_DIR/.env"
echo "2) systemctl start rune-bot"
echo "3) systemctl status rune-bot --no-pager"
