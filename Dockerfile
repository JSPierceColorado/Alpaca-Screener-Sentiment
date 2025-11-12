FROM python:3.12-slim

# Install system deps (if you need more later, add them here)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run the job once and exit
CMD ["python", "main.py"]
