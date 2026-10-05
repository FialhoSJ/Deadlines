# Dockerman Deadline System
FROM python:3.12-slim

LABEL maintainer="Dockerman"
LABEL description="Sistema de deadlines com contagem regressiva contínua"

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=9999 \
    DATA_DIR=/app/data

COPY app.py ./
COPY public/ ./public/

RUN mkdir -p /app/data \
  && useradd --system --uid 10001 --no-create-home appuser \
  && chown -R appuser:appuser /app

USER appuser

EXPOSE 9999

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9999/')" || exit 1

CMD ["python", "app.py"]
