# S3 Scraper

Prometheus exporter for monitoring files in S3-compatible storage. Collects file size, ETag (for change detection), and last modification timestamp.

## Metrics

| Metric | Labels | Description |
|--------|--------|-------------|
| `s3_file_size_bytes` | `bucket`, `key` | File size in bytes |
| `s3_file_etag_hash` | `bucket`, `key` | ETag hash (changes when content changes) |
| `s3_file_last_modified_timestamp` | `bucket`, `key` | Last modified timestamp |
| `s3_files_total` | `bucket`, `prefix` | Total files in bucket/prefix |
| `s3_last_scrape_timestamp` | — | Timestamp of last scrape |
| `s3_scrape_duration_seconds` | — | Duration of last scrape |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `S3_ENDPOINT` | — | S3-compatible storage URL |
| `S3_REGION` | — | Region |
| `AWS_ACCESS_KEY_ID` | — | Access key |
| `AWS_SECRET_ACCESS_KEY` | — | Secret key |
| `S3_BUCKETS` | — | Buckets to scrape: `bucket:prefix,bucket:prefix` |
| `S3_EXCLUDE_PREFIXES` | `archive/` | Prefixes to exclude, comma-separated |
| `SCRAPE_INTERVAL` | `300` | Scrape interval in seconds |
| `EXPORTER_PORT` | `8000` | Metrics port |
| `LOG_LEVEL` | `INFO` | Log level |

## Quick Start

```bash
docker build -t s3-scraper .
docker run -d \
  --name s3-scraper \
  -p 8000:8000 \
  -e S3_ENDPOINT=https://your-s3-endpoint \
  -e S3_REGION=your-region \
  -e AWS_ACCESS_KEY_ID=xxx \
  -e AWS_SECRET_ACCESS_KEY=xxx \
  -e S3_BUCKETS="my-bucket:" \
  s3-scraper

curl http://localhost:8000/metrics
'''

## License

MIT