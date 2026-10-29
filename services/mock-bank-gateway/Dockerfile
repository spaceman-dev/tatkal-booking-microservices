# mock-bank-gateway image
#   docker build -t tatkal/mock-bank-gateway:1.0 services/mock-bank-gateway
# All settings are passed as environment variables when the container starts.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
RUN useradd --system --uid 10001 --no-create-home tatkal

# dependencies first: a code change doesn't reinstall them, and services with the
# same requirements.txt share this layer
COPY requirements.txt .
RUN pip install -r requirements.txt

# owned by root, so the app user can read the code but not change it
COPY app ./app
USER tatkal

ARG APP_VERSION=1.0.0
ENV APP_VERSION=${APP_VERSION} \
    HOST=0.0.0.0 \
    PORT=8099
EXPOSE 8099

LABEL org.opencontainers.image.title="mock-bank-gateway" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.source="https://github.com/spaceman-dev/tatkal-booking-microservices"

HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --start-interval=2s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ['PORT'], timeout=2)"]

# exec form, so python is PID 1 and gets SIGTERM from docker stop / kubectl
CMD ["python", "-m", "app"]
