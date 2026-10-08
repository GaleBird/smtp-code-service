FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY web/ ./web/
COPY smtp_code_service.py .

ENV DOMAIN=example.com \
    API_TOKEN=your-token \
    HTTP_HOST=0.0.0.0 \
    HTTP_PORT=8081 \
    SMTP_HOST=0.0.0.0 \
    SMTP_PORT=25 \
    DB_PATH=/var/lib/smtp-code-service/messages.db

EXPOSE 8081 25

CMD ["python", "-m", "app.main"]
