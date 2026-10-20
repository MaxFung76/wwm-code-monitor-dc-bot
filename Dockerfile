FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN adduser --uid 1000 --disabled-password --gecos "" appuser

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --upgrade pip && \
    python -m pip install --no-cache-dir .

RUN mkdir -p /app/data && chown -R appuser:appuser /app

USER appuser

CMD ["python", "-m", "wwm_codebot.main"]
