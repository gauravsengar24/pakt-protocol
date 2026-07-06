# PAKT Protocol — Hugging Face Spaces Dockerfile
# Runs the Streamlit web interface for the AI Agent Pact Engine demo.

FROM python:3.11-slim

WORKDIR /app

# System dependencies for kaspa SDK compilation (optional)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
        streamlit \
        # Try kaspa SDK (pre-built wheels for Python 3.11)
        kaspa \
        # Fallback dependencies for pure-Python mode
        ecdsa \
        websockets \
        2>&1 | tail -5

# Allow fallback to pure-Python mode if kaspa SDK fails
RUN python3 -c "import kaspa" 2>/dev/null && \
    echo "kaspa SDK loaded" || \
    echo "kaspa SDK not available — using pure-Python mode"

# Streamlit configuration
ENV STREAMLIT_SERVER_PORT=7860
ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0
ENV STREAMLIT_SERVER_HEADLESS=true
ENV STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
ENV STREAMLIT_THEME_BASE=dark

# Expose the HF Spaces port
EXPOSE 7860

# Health check
HEALTHCHECK CMD curl --fail http://localhost:7860/_stcore/health || exit 1

# Run the Streamlit app
CMD ["streamlit", "run", "app/streamlit_app.py"]
