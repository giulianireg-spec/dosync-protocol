FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY dosync/ ./dosync/
COPY server.py .
COPY dashboard.html .
COPY manage.py .
COPY certify.py .

# Create data directory
RUN mkdir -p /data

# Environment defaults
ENV DOSYNC_AUTH=false
ENV DOSYNC_DB_PATH=/data/dosync.db
ENV PYTHONPATH=/app

EXPOSE 47200

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "47200"]
