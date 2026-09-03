# ---- build stage: compile wheels with toolchain, kept out of runtime image ----
FROM python:3.12-slim-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# ---- runtime stage: OCR toolchain (tesseract + ghostscript) but no compiler ----
FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-eng ghostscript unpaper pngquant \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

COPY . .

EXPOSE 5000

#   --preload          : import app once in master (also serializes migrations)
#   --graceful-timeout : lets a running OCR thread finish before a worker recycles
#   OMP_THREAD_LIMIT   : stop tesseract spawning threads that fight the CPU limit
ENV GUNICORN_WORKERS=2 \
    GUNICORN_THREADS=4 \
    GUNICORN_TIMEOUT=300 \
    GUNICORN_GRACEFUL_TIMEOUT=600 \
    GUNICORN_MAX_REQUESTS=2000 \
    GUNICORN_MAX_REQUESTS_JITTER=200 \
    STATEMENTS_DIR=/data/statements \
    OMP_THREAD_LIMIT=1

CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:5000 \
    --workers ${GUNICORN_WORKERS} \
    --threads ${GUNICORN_THREADS} \
    --timeout ${GUNICORN_TIMEOUT} \
    --graceful-timeout ${GUNICORN_GRACEFUL_TIMEOUT} \
    --max-requests ${GUNICORN_MAX_REQUESTS} \
    --max-requests-jitter ${GUNICORN_MAX_REQUESTS_JITTER} \
    --worker-tmp-dir /dev/shm \
    --preload \
    'app:create_app()'"]
