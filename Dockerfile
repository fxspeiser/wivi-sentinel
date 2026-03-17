FROM python:3.11-slim-bookworm

WORKDIR /app

# System deps: scipy needs libgfortran/openblas, scapy needs libpcap
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgfortran5 libopenblas0 libpcap-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY engine/ ./engine/
COPY dist/   ./dist/

# data/ is mounted as a volume at runtime — don't bake profiles into the image
RUN mkdir -p data

ENV FLASK_PORT=5555
EXPOSE 5555

CMD ["python3", "server.py"]
