FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Create required directories and set ownership
RUN mkdir -p Media user_sessions backups logs && \
    chown -R appuser:appuser /app && \
    chmod -R 700 Media user_sessions backups logs

USER appuser

VOLUME ["/app/Media", "/app/user_sessions", "/app/backups", "/app/logs"]

CMD ["python", "bot.py"]