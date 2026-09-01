#!/usr/bin/env bash
# yt-dlp breaks whenever a site changes its player, which is the most likely
# reason this bot ever stops working. Run daily from cron:
#   0 5 * * * $HOME/social-download-tg/deploy/update-ytdlp.sh >> $HOME/social-download-tg/data/ytdlp-update.log 2>&1
# cron gives you almost no PATH, so everything here is an absolute path.
set -euo pipefail

project_dir="$(cd "$(dirname "$0")/.." && pwd)"
# pm2 may be installed by npm, bun, or on the PATH already.
pm2_bin="$(command -v pm2 || true)"
for candidate in "$HOME/.bun/bin/pm2" "$HOME/.npm-global/bin/pm2" /usr/local/bin/pm2; do
  [ -n "$pm2_bin" ] && break
  [ -x "$candidate" ] && pm2_bin="$candidate"
done

cd "$project_dir"
before="$(.venv/bin/yt-dlp --version 2>/dev/null || echo none)"
.venv/bin/pip install --quiet --upgrade yt-dlp
after="$(.venv/bin/yt-dlp --version)"

if [ "$before" = "$after" ]; then
  echo "$(date -Is) yt-dlp already current ($after)"
  exit 0
fi

# Only bounce the bot when something actually changed.
"$pm2_bin" restart social-download-tg >/dev/null
echo "$(date -Is) yt-dlp $before -> $after, bot restarted"
