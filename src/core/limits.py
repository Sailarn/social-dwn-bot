"""Numbers imposed on us from outside, or chosen once and depended on widely."""

# Telegram's own ceilings.
TELEGRAM_UPLOAD_LIMIT_MB = 50
PHOTO_UPLOAD_LIMIT_BYTES = 10 * 1024 * 1024  # sendPhoto is stricter than sendVideo
ALBUM_MAX_ITEMS = 10  # media group ceiling

# A carousel of videos could otherwise cost hundreds of megabytes of a home
# connection for one message. Items are added in order until this is reached.
ALBUM_TOTAL_BYTES = 50 * 1024 * 1024

# Container overhead and yt-dlp's size estimates are both approximate, so aim
# below the hard ceiling rather than at it.
SIZE_TARGET_MARGIN_BYTES = 3 * 1024 * 1024

# Re-encoding an oversized clip.
AUDIO_BITRATE_KBPS = 128
MAX_ENCODED_HEIGHT = 720
REENCODE_TIMEOUT_SECONDS = 900

# Fetching a photo post's images.
IMAGE_FETCH_TIMEOUT_SECONDS = 60
IMAGE_USER_AGENT = "Mozilla/5.0"
