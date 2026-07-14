FROM python:3.12.11-alpine

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

RUN adduser -D -u 1000 appuser && \
    mkdir -p /app && \
    chown -R appuser:appuser /app

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV UV_CACHE_DIR=/tmp/.uv-cache
ENV PYTHONPATH=/app


COPY --chown=appuser:appuser pyproject.toml uv.lock ./

USER appuser

RUN uv sync --frozen --no-cache

COPY --chown=appuser:appuser . .

RUN chmod +x start.sh

EXPOSE 8004

CMD ["./start.sh"]
