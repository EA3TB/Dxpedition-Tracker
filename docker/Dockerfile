FROM python:3.12-slim

WORKDIR /app

# System deps — mdbtools for Swisslog MDB support on Linux
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    mdbtools \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# Data dir will be mounted as volume
RUN mkdir -p /opt/Dxpedition_Dashboard

EXPOSE 8766

CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8766"]
