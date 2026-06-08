FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY src/ ./src/
COPY config/ ./config/
COPY docs/ ./docs/

# Run
CMD ["python", "-m", "src.main", "daemon"]
