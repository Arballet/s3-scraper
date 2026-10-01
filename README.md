# s3-scraper

Prometheus exporter for files in S3-compatible storage.

It was written to answer one question: is the sync job still putting fresh files into the bucket? The exporter lists the configured buckets every few minutes and publishes file sizes, modification times and a hash of the ETag, so you can alert when the newest file is too old, when a file stopped changing or when files disappear.

## Metrics

Per bucket/prefix (labels `bucket`, `prefix`):

- `s3_files_total`
- `s3_prefix_size_bytes`
- `s3_prefix_last_modified_timestamp`: modification time of the newest file
- `s3_scrape_success`: 1 if the last listing worked

Per file (labels `bucket`, `key`):

- `s3_file_size_bytes`
- `s3_file_last_modified_timestamp`
- `s3_file_etag_hash`: a number derived from the ETag, it changes when the content changes

Plus `s3_last_scrape_timestamp` and `s3_scrape_duration_seconds`.

Per-file metrics mean three series for every file. That is fine for hundreds of files; for big prefixes turn them off with `S3_PER_FILE_METRICS=false` and use the per-prefix ones.

When a file is deleted its series are removed on the next scrape. When a listing fails (no access, storage is down) the old values stay and `s3_scrape_success` drops to 0, so an outage doesn't look like an empty bucket.

## Running

```bash
docker build -t s3-scraper .
docker run -d \
  --name s3-scraper \
  -p 8000:8000 \
  -e S3_ENDPOINT=https://s3.example.com \
  -e S3_REGION=us-east-1 \
  -e AWS_ACCESS_KEY_ID=xxx \
  -e AWS_SECRET_ACCESS_KEY=xxx \
  -e S3_BUCKETS="my-bucket:incoming/,other-bucket" \
  s3-scraper

curl http://localhost:8000/metrics
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `S3_BUCKETS` | | `bucket:prefix,bucket:prefix`. Without a prefix the whole bucket is listed. |
| `S3_EXCLUDE_PREFIXES` | `archive/` | Key prefixes to skip, comma-separated |
| `S3_ENDPOINT` | AWS | Storage URL |
| `S3_REGION` | | Region |
| `S3_ADDRESSING_STYLE` | `virtual` | `virtual`, `path` or `auto`. MinIO and Ceph usually need `path`. |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | | If empty, the usual AWS credential chain is used (IAM role, web identity, profile) |
| `S3_PER_FILE_METRICS` | `true` | Export `s3_file_*` |
| `SCRAPE_INTERVAL` | `300` | Seconds |
| `EXPORTER_PORT` | `8000` | Metrics port |
| `LOG_LEVEL` | `INFO` | |

The key only needs `s3:ListBucket`. File contents are never downloaded.

## Alerts

```yaml
# nothing new under the prefix for a day
- alert: S3SyncStale
  expr: time() - s3_prefix_last_modified_timestamp{bucket="my-bucket", prefix="incoming"} > 86400

# a file that should be refreshed daily hasn't changed
- alert: S3FileNotUpdated
  expr: changes(s3_file_etag_hash{key="incoming/report.csv"}[1d]) == 0

- alert: S3ScrapeFailing
  expr: s3_scrape_success == 0
  for: 15m

- alert: S3ScraperStalled
  expr: time() - s3_last_scrape_timestamp > 900
```

## Image

boto3 pulls in API descriptions for every AWS service. The Dockerfile deletes all of them except S3, which takes the dependencies from about 32 MB down to 7 MB.

Multi-arch build:

```bash
docker buildx build --platform linux/amd64,linux/arm64 -t <registry>/s3-scraper:<tag> --push .
```

Kubernetes example: [deploy/kubernetes.yaml](deploy/kubernetes.yaml). More than one replica works, each one lists the buckets on its own.

Tests: `pip install -r requirements.txt pytest && pytest`

## License

MIT
