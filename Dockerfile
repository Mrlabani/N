FROM python:3.11-slim

RUN apt update && apt install -y \
    ffmpeg \
    libtorrent-rasterbar-dev \
    python3-libtorrent \
    && pip install --no-cache-dir -U pip

WORKDIR /app
COPY . /app
RUN pip install -r requirements.txt

CMD ["python", "torrent_bot.py"]
