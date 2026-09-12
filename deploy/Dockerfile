FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# ffmpeg: audio conversion (mp3 -> ogg voice notes) and playback;
# build-essential: wheels that need compiling (e.g. faster-whisper deps)
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --upgrade pip \
 && pip install -r requirements.txt

COPY . .

# SQLite memory, TTS cache, logs live here (mounted as a volume)
RUN mkdir -p /app/data/logs /app/workspace

EXPOSE 8788

# telegram + web (0.0.0.0:8788) + scheduler in one process.
# The voice CLI is Mac-only and not run in the container.
CMD ["python", "run.py", "all"]
