FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 app
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py ./
COPY src/ ./src/

RUN mkdir -p /data && chown -R app:app /data /app
USER app

ENV PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    PORT=7860

EXPOSE 7860
CMD ["python", "bot.py"]
