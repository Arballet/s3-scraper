FROM python:3.11-alpine

WORKDIR /app

RUN apk add --no-cache ca-certificates

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ .

RUN adduser -D -u 1000 scraper && \
    chown -R scraper:scraper /app

USER scraper

EXPOSE 8000

ENTRYPOINT ["python", "app.py"]