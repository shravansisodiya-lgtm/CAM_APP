FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 TZ=Asia/Kolkata DATA_DIR=/data COOKIE_SECURE=0
RUN apt-get update && apt-get install -y --no-install-recommends iputils-ping tzdata \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /data
VOLUME /data
EXPOSE 5000
# ONE worker only: the ping/alert thread starts with the app (more workers = duplicate alerts).
CMD ["sh", "-c", "gunicorn -w 1 --threads 4 -b 0.0.0.0:${PORT:-5000} app:app"]
