FROM python:3.11-slim

WORKDIR /app

# curl for debugging; the rest are WeasyPrint's native deps for the tour-blog PDF export
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libcairo2 libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libffi-dev \
    fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/
COPY scripts/ ./scripts/

# Create data directory for persistent volume mount
RUN mkdir -p /data

# Run as non-root user for security
RUN useradd -m -u 1000 ascent && chown -R ascent:ascent /app /data
USER ascent

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
