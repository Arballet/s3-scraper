#!/usr/bin/env python3
import hashlib
import os
import signal
import sys
import threading
import time
import logging
from typing import Dict, List, Optional

import boto3
from botocore.config import Config
from prometheus_client import start_http_server, Gauge

S3_ENDPOINT = os.environ.get('S3_ENDPOINT', '')
S3_REGION = os.environ.get('S3_REGION', '')
S3_ADDRESSING_STYLE = os.environ.get('S3_ADDRESSING_STYLE', 'virtual')
AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID', '')
AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY', '')
S3_BUCKETS = os.environ.get('S3_BUCKETS', '')
S3_EXCLUDE_PREFIXES = os.environ.get('S3_EXCLUDE_PREFIXES', 'archive/')
S3_PER_FILE_METRICS = os.environ.get('S3_PER_FILE_METRICS', 'true').lower() in ('1', 'true', 'yes')
SCRAPE_INTERVAL = int(os.environ.get('SCRAPE_INTERVAL', 300))
EXPORTER_PORT = int(os.environ.get('EXPORTER_PORT', 8000))
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger('s3-scraper')

stop_event = threading.Event()


class S3Scraper:

    def __init__(self, s3_client=None):
        self.s3_client = s3_client or self._init_s3()
        self.buckets = self._parse_buckets()
        self.exclude_prefixes = self._parse_exclude_prefixes()
        # keys exported on the previous scrape, used to drop series of deleted files
        self._known_keys: Dict[tuple, set] = {}
        self._init_metrics()

    def _init_s3(self) -> boto3.client:
        try:
            # empty values fall back to the default boto3 chain (AWS endpoint, IAM role, profile)
            return boto3.client(
                's3',
                endpoint_url=S3_ENDPOINT or None,
                aws_access_key_id=AWS_ACCESS_KEY_ID or None,
                aws_secret_access_key=AWS_SECRET_ACCESS_KEY or None,
                region_name=S3_REGION or None,
                config=Config(
                    signature_version='s3v4',
                    s3={'addressing_style': S3_ADDRESSING_STYLE},
                    connect_timeout=10,
                    read_timeout=60,
                    retries={'max_attempts': 3, 'mode': 'standard'}
                )
            )
        except Exception as e:
            logger.error(f"Failed to init S3 client: {e}")
            sys.exit(1)

    def _parse_buckets(self) -> List[Dict[str, str]]:
        buckets = []

        if S3_BUCKETS:
            for item in S3_BUCKETS.split(','):
                item = item.strip()
                if not item:
                    continue

                if ':' in item:
                    bucket, prefix = item.split(':', 1)
                else:
                    bucket, prefix = item, ''

                buckets.append({
                    'bucket': bucket.strip(),
                    'prefix': prefix.strip().rstrip('/')
                })

        return buckets

    def _parse_exclude_prefixes(self) -> List[str]:
        if S3_EXCLUDE_PREFIXES:
            return [p.strip() for p in S3_EXCLUDE_PREFIXES.split(',') if p.strip()]
        return []

    def _init_metrics(self):

        self.metric_file_size = Gauge(
            's3_file_size_bytes',
            'File size in bytes',
            ['bucket', 'key']
        )

        self.metric_file_etag = Gauge(
            's3_file_etag_hash',
            'ETag hash of file (changes when content changes)',
            ['bucket', 'key']
        )

        self.metric_file_last_modified = Gauge(
            's3_file_last_modified_timestamp',
            'Last modified timestamp',
            ['bucket', 'key']
        )

        self.metric_files_count = Gauge(
            's3_files_total',
            'Total number of files in bucket/prefix',
            ['bucket', 'prefix']
        )

        self.metric_prefix_size = Gauge(
            's3_prefix_size_bytes',
            'Total size of files in bucket/prefix',
            ['bucket', 'prefix']
        )

        self.metric_prefix_last_modified = Gauge(
            's3_prefix_last_modified_timestamp',
            'Last modified timestamp of the newest file in bucket/prefix',
            ['bucket', 'prefix']
        )

        self.metric_scrape_success = Gauge(
            's3_scrape_success',
            '1 if the last listing of bucket/prefix succeeded',
            ['bucket', 'prefix']
        )

        self.metric_last_scrape = Gauge(
            's3_last_scrape_timestamp',
            'Timestamp of last scrape'
        )
        self.metric_scrape_duration = Gauge(
            's3_scrape_duration_seconds',
            'Duration of last scrape'
        )

    def _etag_to_number(self, etag: str) -> float:
        if not etag:
            return 0.0
        hash_bytes = hashlib.md5(etag.encode()).digest()[:8]
        return float(int.from_bytes(hash_bytes, 'big')) / 1e19

    def _is_excluded(self, key: str) -> bool:
        for prefix in self.exclude_prefixes:
            if key.startswith(prefix):
                return True
        return False

    def list_files(self, bucket: str, prefix: str = '') -> Optional[List[Dict]]:
        """Returns the files under bucket/prefix, or None if the listing failed."""
        files = []

        try:
            paginator = self.s3_client.get_paginator('list_objects_v2')
            kwargs = {'Bucket': bucket}
            if prefix:
                kwargs['Prefix'] = prefix

            for page in paginator.paginate(**kwargs):
                if 'Contents' not in page:
                    continue

                for obj in page['Contents']:
                    if obj['Key'].endswith('/'):
                        continue
                    if self._is_excluded(obj['Key']):
                        continue

                    files.append({
                        'key': obj['Key'],
                        'size': obj['Size'],
                        'last_modified': obj['LastModified'],
                        'etag': obj.get('ETag', '').strip('"')
                    })

            logger.info(f"s3://{bucket}/{prefix} -> {len(files)} files")

        except Exception as e:
            logger.error(f"Error listing s3://{bucket}/{prefix}: {e}")
            return None

        return files

    def update_metrics(self, bucket: str, prefix: str, files: List[Dict]):
        prefix_label = prefix or '/'

        self.metric_files_count.labels(bucket=bucket, prefix=prefix_label).set(len(files))
        self.metric_prefix_size.labels(bucket=bucket, prefix=prefix_label).set(sum(f['size'] for f in files))
        if files:
            newest = max(f['last_modified'] for f in files)
            self.metric_prefix_last_modified.labels(bucket=bucket, prefix=prefix_label).set(newest.timestamp())

        if not S3_PER_FILE_METRICS:
            return

        current_keys = set()
        for f in files:
            key = f['key']
            current_keys.add(key)

            self.metric_file_size.labels(
                bucket=bucket, key=key
            ).set(f['size'])

            etag_num = self._etag_to_number(f['etag'])
            self.metric_file_etag.labels(
                bucket=bucket, key=key
            ).set(etag_num)

            self.metric_file_last_modified.labels(
                bucket=bucket, key=key
            ).set(f['last_modified'].timestamp())

        for key in self._known_keys.get((bucket, prefix), set()) - current_keys:
            for metric in (self.metric_file_size, self.metric_file_etag, self.metric_file_last_modified):
                try:
                    metric.remove(bucket, key)
                except KeyError:
                    # the same key may be covered by another configured prefix
                    pass

        self._known_keys[(bucket, prefix)] = current_keys

    def scrape(self):
        logger.info("Scraping started")
        start = time.time()

        if not self.buckets:
            logger.warning("No buckets configured (S3_BUCKETS is empty)")
            self.metric_last_scrape.set(time.time())
            return

        total = 0
        for cfg in self.buckets:
            bucket = cfg['bucket']
            prefix = cfg['prefix']

            files = self.list_files(bucket, prefix)
            success = self.metric_scrape_success.labels(bucket=bucket, prefix=prefix or '/')
            if files is None:
                # keep the previous values, a failed listing is not an empty bucket
                success.set(0)
                continue

            success.set(1)
            self.update_metrics(bucket, prefix, files)
            total += len(files)

        elapsed = time.time() - start
        self.metric_last_scrape.set(time.time())
        self.metric_scrape_duration.set(elapsed)

        logger.info(f"Scraped {total} files in {elapsed:.2f}s")

    def run(self):
        logger.info(f"S3 Scraper starting on :{EXPORTER_PORT}")
        logger.info(f"Buckets: {self.buckets or 'NOT SET'}")
        logger.info(f"Exclude prefixes: {self.exclude_prefixes or 'none'}")
        logger.info(f"Interval: {SCRAPE_INTERVAL}s")

        self.scrape()

        while not stop_event.wait(SCRAPE_INTERVAL):
            self.scrape()

        logger.info("Shutting down")


def main():
    signal.signal(signal.SIGTERM, lambda *_: stop_event.set())
    signal.signal(signal.SIGINT, lambda *_: stop_event.set())

    scraper = S3Scraper()
    start_http_server(EXPORTER_PORT)
    scraper.run()


if __name__ == '__main__':
    main()
