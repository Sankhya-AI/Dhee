FROM python:3.11-slim

WORKDIR /app

# Copy only the package sources needed for an installable Dhee image.
COPY pyproject.toml README.md LICENSE MANIFEST.in ./
COPY dhee/ ./dhee/
COPY dhee_shared/ ./dhee_shared/
COPY engram/ ./engram/
COPY engram-bus/ ./engram-bus/

# Install a slim MCP server by default. For semantic memory use:
# docker build --build-arg EXTRAS="nvidia,zvec,mcp" .
ARG EXTRAS="mcp"
RUN pip install --no-cache-dir -e ".[$EXTRAS]"

# Create data directory
RUN mkdir -p /data/dhee

# Environment
ENV DHEE_DATA_DIR=/data/dhee
ENV PYTHONUNBUFFERED=1

# Health check
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import dhee; print(dhee.__version__)" || exit 1

VOLUME /data

# Default: MCP server (4 tools)
CMD ["dhee-mcp"]
