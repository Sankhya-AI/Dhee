FROM python:3.11-slim

WORKDIR /app

# Copy project files
COPY pyproject.toml README.md ./
COPY dhee/ ./dhee/
COPY dhee/ ./dhee/

# Install package — slim by default (no llama.cpp, no local models)
# Use: docker build --build-arg EXTRAS="openai,mcp" .
ARG EXTRAS="openai,mcp"
RUN pip install --no-cache-dir -e ".[$EXTRAS]"

# Create data directory
RUN mkdir -p /data/dhee

# Environment
ENV DHEE_DATA_DIR=/data/dhee
ENV PYTHONUNBUFFERED=1

# Health check
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "from dhee.core.buddhi import Buddhi; Buddhi(); print('ok')" || exit 1

VOLUME /data

# Default: MCP server (4 tools)
CMD ["dhee-mcp"]
