# MeshForge Docker Container
# Build: docker build -t meshforge .
# Run:   docker run -p 8880:8880 meshforge

FROM python:3.11-slim-bookworm

LABEL maintainer="WH6GXZ"
LABEL description="MeshForge - Mesh Network Operations Center"
LABEL version="0.4.6-beta"

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY templates/ ./templates/
COPY examples/ ./examples/
COPY assets/ ./assets/
COPY README.md .
COPY CLAUDE.md .

# Make scripts executable
RUN chmod +x scripts/*.sh 2>/dev/null || true

# Create non-root user for security
RUN useradd -m -s /bin/bash meshforge && \
    chown -R meshforge:meshforge /app

# Switch to non-root user
USER meshforge

# Expose web UI port
EXPOSE 8880

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8880/api/status || exit 1

# Default command - web UI
CMD ["python3", "src/main_web.py", "--host", "0.0.0.0", "--port", "8880"]
