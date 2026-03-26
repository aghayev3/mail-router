# ── Build stage ───────────────────────────────────────────────────────────────
# Using slim to minimise image size and attack surface.
FROM python:3.12-slim AS base

# Don't write .pyc files, don't buffer stdout (logs appear immediately)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Create a non-root user — never run your app as root inside a container
RUN groupadd --gid 1001 appgroup && \
    useradd  --uid 1001 --gid appgroup --shell /bin/bash --create-home appuser

WORKDIR /app

# Install dependencies first (separate layer = cached unless requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Give the non-root user ownership of the app directory
# (needed to write fallback_queue.jsonl and email_router.log)
RUN chown -R appuser:appgroup /app

USER appuser

# Health check — verifies the process is still running
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD pgrep -f "python main.py" || exit 1

CMD ["python", "main.py"]
