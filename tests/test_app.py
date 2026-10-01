import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from prometheus_client import REGISTRY

os.environ["S3_BUCKETS"] = "data:incoming/, backup"
os.environ["S3_EXCLUDE_PREFIXES"] = "incoming/archive/, tmp/"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import app as scraper_app  # noqa: E402


def obj(key, size=10, etag='"abc"', day=1):
    return {"Key": key, "Size": size, "ETag": etag, "LastModified": datetime(2026, 1, day, tzinfo=timezone.utc)}


def value(name, **labels):
    return REGISTRY.get_sample_value(name, labels)


@pytest.fixture(scope="module")
def scraper():
    return scraper_app.S3Scraper(s3_client=MagicMock())


def set_listing(scraper, listing):
    """listing: {bucket: [objects]} or an Exception to raise."""
    def paginate(**kwargs):
        result = listing[kwargs["Bucket"]]
        if isinstance(result, Exception):
            raise result
        return [{"Contents": result}] if result else [{}]

    scraper.s3_client.get_paginator.return_value.paginate.side_effect = paginate


def test_parse_config(scraper):
    assert scraper.buckets == [{"bucket": "data", "prefix": "incoming"}, {"bucket": "backup", "prefix": ""}]
    assert scraper.exclude_prefixes == ["incoming/archive/", "tmp/"]


def test_scrape_exports_file_and_prefix_metrics(scraper):
    set_listing(scraper, {
        "data": [
            obj("incoming/a.csv", size=100, day=1),
            obj("incoming/b.csv", size=50, day=3),
            obj("incoming/folder/"),
            obj("incoming/archive/old.csv"),
        ],
        "backup": [],
    })
    scraper.scrape()

    assert scraper.s3_client.get_paginator.return_value.paginate.call_args_list[0].kwargs == {
        "Bucket": "data", "Prefix": "incoming",
    }
    assert value("s3_files_total", bucket="data", prefix="incoming") == 2
    assert value("s3_prefix_size_bytes", bucket="data", prefix="incoming") == 150
    newest = datetime(2026, 1, 3, tzinfo=timezone.utc).timestamp()
    assert value("s3_prefix_last_modified_timestamp", bucket="data", prefix="incoming") == newest
    assert value("s3_file_size_bytes", bucket="data", key="incoming/a.csv") == 100
    assert value("s3_file_size_bytes", bucket="data", key="incoming/archive/old.csv") is None
    assert value("s3_scrape_success", bucket="data", prefix="incoming") == 1

    assert value("s3_files_total", bucket="backup", prefix="/") == 0
    assert value("s3_scrape_success", bucket="backup", prefix="/") == 1
    assert value("s3_last_scrape_timestamp") > 0


def test_deleted_file_series_are_removed(scraper):
    set_listing(scraper, {"data": [obj("incoming/b.csv", size=50, day=3)], "backup": []})
    scraper.scrape()

    assert value("s3_files_total", bucket="data", prefix="incoming") == 1
    assert value("s3_file_size_bytes", bucket="data", key="incoming/a.csv") is None
    assert value("s3_file_etag_hash", bucket="data", key="incoming/a.csv") is None
    assert value("s3_file_size_bytes", bucket="data", key="incoming/b.csv") == 50


def test_failed_listing_keeps_previous_values(scraper):
    set_listing(scraper, {"data": Exception("access denied"), "backup": []})
    scraper.scrape()

    assert value("s3_scrape_success", bucket="data", prefix="incoming") == 0
    assert value("s3_files_total", bucket="data", prefix="incoming") == 1
    assert value("s3_file_size_bytes", bucket="data", key="incoming/b.csv") == 50


def test_etag_hash_changes_with_content(scraper):
    first = scraper._etag_to_number("abc")
    assert first == scraper._etag_to_number("abc")
    assert first != scraper._etag_to_number("abd")
    assert scraper._etag_to_number("") == 0.0


def test_per_file_metrics_can_be_disabled(scraper, monkeypatch):
    monkeypatch.setattr(scraper_app, "S3_PER_FILE_METRICS", False)
    files = [{"key": "x.bin", "size": 5, "etag": "abc", "last_modified": datetime(2026, 1, 1, tzinfo=timezone.utc)}]
    scraper.update_metrics("other", "", files)

    assert value("s3_files_total", bucket="other", prefix="/") == 1
    assert value("s3_file_size_bytes", bucket="other", key="x.bin") is None
