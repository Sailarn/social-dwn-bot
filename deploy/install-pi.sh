#!/usr/bin/env bash
# One-shot install. Run from the project directory on the target machine.
#
# Works on Debian/Raspberry Pi OS and anything else with apt. On other systems,
# install ffmpeg yourself and the rest still applies.
set -euo pipefail

if [ ! -f .env ]; then
  echo "Create .env first:  cp .env.example .env  then add your BOT_TOKEN" >&2
  exit 1
fi
if ! grep -q '^BOT_TOKEN=.\+' .env; then
  echo "BOT_TOKEN is empty in .env" >&2
  exit 1
fi
chmod 600 .env

if ! command -v ffmpeg >/dev/null; then
  echo "==> installing ffmpeg"
  sudo apt-get update -qq
  sudo apt-get install -y --no-install-recommends ffmpeg python3-venv
fi

echo "==> creating virtualenv"
python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

pm2_bin="$(command -v pm2 || true)"
for candidate in "$HOME/.bun/bin/pm2" "$HOME/.npm-global/bin/pm2" /usr/local/bin/pm2; do
  [ -n "$pm2_bin" ] && break
  [ -x "$candidate" ] && pm2_bin="$candidate"
done

if [ -n "$pm2_bin" ]; then
  echo "==> starting under pm2"
  "$pm2_bin" start ecosystem.config.js
  "$pm2_bin" save
  echo
  echo "Done. Logs:  $pm2_bin logs social-download-tg"
else
  cat <<MESSAGE

pm2 was not found, so the bot has not been started. Either:

  install pm2      npm install -g pm2   (then re-run this script)
  or use systemd   sed -e "s|__USER__|\$USER|g" -e "s|__DIR__|\$PWD|g" \\
                       deploy/social-download-tg.service \\
                     | sudo tee /etc/systemd/system/social-download-tg.service
                   sudo systemctl enable --now social-download-tg

  or just run it   .venv/bin/python bot.py
MESSAGE
fi

echo
echo "Reminder: yt-dlp breaks when a platform changes. Add the daily updater:"
echo "  0 5 * * * $PWD/deploy/update-ytdlp.sh >> $PWD/data/ytdlp-update.log 2>&1"
