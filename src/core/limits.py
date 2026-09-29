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

# A video converted for playability is capped at this frame rate: Instagram's
# 60 fps streams cost twice the CPU for nothing a phone chat shows.
CONVERTED_MAX_FPS = 30

# Apify runs a scraper per request: starting it is most of the wait.
APIFY_TIMEOUT_SECONDS = 180
APIFY_RUN_TIMEOUT_SECONDS = 150

# A video fetched straight from a CDN, before any re-encode. Past this it is not
# worth the disk or the transcode on a small host.
DIRECT_VIDEO_MAX_BYTES = 200 * 1024 * 1024
DIRECT_FETCH_CHUNK_BYTES = 256 * 1024

# Budgets /stats measures against. Apify's free plan: $5 of credit a month, its
# Instagram actor (data-slayer/instagram-post-details) about $0.0045 a post:
# $0.002 to start plus $0.0025 per result. Render's free plan: 100 GB out a month.
APIFY_MONTHLY_CREDIT_USD = 5.0
APIFY_COST_PER_RUN_USD = 0.0045
RENDER_MONTHLY_BANDWIDTH_BYTES = 100 * 1024 ** 3
