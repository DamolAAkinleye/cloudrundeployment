import datetime
import json
import logging
import os
from pathlib import Path
import random
import sys
import time

import cowsay
import requests
from google.cloud import storage



LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s [task=%(task_index)s attempt=%(task_attempt)s] %(message)s",
)
BASE_LOGGER = logging.getLogger("cloud_run_job")


class TaskContextFilter(logging.Filter):
    """Injects task metadata into every log record."""

    def filter(self, record):
        record.task_index = os.getenv("CLOUD_RUN_TASK_INDEX", "unknown")
        record.task_attempt = os.getenv("CLOUD_RUN_TASK_ATTEMPT", "unknown")
        return True


BASE_LOGGER.addFilter(TaskContextFilter())


def get_env(name, default="0"):
    """Reads environment variables and logs their resolved values."""
    value = os.getenv(name, default)
    BASE_LOGGER.debug("Resolved environment variable %s=%s", name, value)
    return value


def parse_float(name, value, default=0.0):
    """Parses numeric env vars with defensive logging."""
    try:
        parsed = float(value)
        BASE_LOGGER.info("Parsed %s=%s", name, parsed)
        return parsed
    except (TypeError, ValueError):
        BASE_LOGGER.error(
            "Invalid numeric value for %s=%s. Falling back to default=%s",
            name,
            value,
            default,
        )
        return default


def load_config():
    """Loads JSON config and prints each key/value setting."""
    config_file = os.getenv("CONFIG_FILE", "config.json")
    config_path = Path(__file__).resolve().parent / config_file
    BASE_LOGGER.info("Loading config from %s", config_path)

    try:
        with config_path.open("r", encoding="utf-8") as fp:
            config = json.load(fp)
    except FileNotFoundError:
        BASE_LOGGER.warning("Config file not found at %s", config_path)
        return {}
    except json.JSONDecodeError as err:
        BASE_LOGGER.error("Invalid JSON in config file %s: %s", config_path, err)
        return {}

    if not isinstance(config, dict):
        BASE_LOGGER.warning("Config content must be a JSON object")
        return {}

    for key in sorted(config):
        value = config[key]
        print(f"CONFIG {key}={value}")
        BASE_LOGGER.info("Config setting %s=%s", key, value)

    return config

cowsay.cow('Hello World')
# Retrieve Job-defined env vars
TASK_INDEX = get_env("CLOUD_RUN_TASK_INDEX", "0")
TASK_ATTEMPT = get_env("CLOUD_RUN_TASK_ATTEMPT", "0")
# Retrieve User-defined env vars
SLEEP_MS = get_env("SLEEP_MS", "0")
FAIL_RATE = get_env("FAIL_RATE", "0")

LOGGER = logging.LoggerAdapter(
    BASE_LOGGER,
    {"task_index": TASK_INDEX, "task_attempt": TASK_ATTEMPT},
)


# Define main script
def main(sleep_ms=0, fail_rate=0, config=None):
    """Program that simulates work using the sleep method and random failures,
    then extracts Gutendex data to GCS.

    Args:
        sleep_ms: number of milliseconds to sleep
        fail_rate: rate of simulated errors
        config: loaded config dict (must contain 'gcs_bucket')
    """
    start_time = time.time()
    LOGGER.info(
        "Starting task execution with sleep_ms=%s and fail_rate=%s",
        sleep_ms,
        fail_rate,
    )

    sleep_seconds = parse_float("SLEEP_MS", sleep_ms, 0.0) / 1000
    LOGGER.debug("Computed sleep duration in seconds: %s", sleep_seconds)

    # Simulate work by waiting for a specific amount of time
    LOGGER.info("Sleeping for %s seconds", sleep_seconds)
    time.sleep(sleep_seconds)  # Convert to seconds

    # Simulate errors

    # Extract Gutendex books to GCS
    cfg = config or {}
    bucket_name = cfg.get("gcs_bucket", "")
    LOGGER.info("Starting Gutendex extraction to GCS bucket: %s", bucket_name)
    extract_to_gcs(bucket_name)

    duration_ms = int((time.time() - start_time) * 1000)
    LOGGER.info("Completed task successfully in %sms", duration_ms)


GUTENDEX_URL = "https://gutendex.com/books"


def extract_to_gcs(bucket_name: str, page_limit: int | None = None) -> None:
    """Fetches all pages from the Gutendex /books API and uploads each page
    as a newline-delimited JSON file to the specified GCS bucket.

    Args:
        bucket_name: Name of the destination GCS bucket (from config.json).
        page_limit: Optional cap on the number of pages to fetch (useful for
                    testing; pass None to fetch all pages).
    """
    if not bucket_name:
        raise ValueError("gcs_bucket must be set in config.json")

    gcs_client = storage.Client()
    bucket = gcs_client.bucket(bucket_name)
    run_ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    url: str | None = GUTENDEX_URL
    page_num = 0

    while url:
        LOGGER.info("Fetching Gutendex page %s from %s", page_num + 1, url)
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        payload = response.json()

        books = payload.get("results", [])
        blob_name = f"gutendex/{run_ts}/page_{page_num:05d}.ndjson"
        ndjson_data = "\n".join(json.dumps(book) for book in books)

        blob = bucket.blob(blob_name)
        blob.upload_from_string(ndjson_data, content_type="application/x-ndjson")
        LOGGER.info(
            "Uploaded %s records to gs://%s/%s", len(books), bucket_name, blob_name
        )

        page_num += 1
        url = payload.get("next")

        if page_limit is not None and page_num >= page_limit:
            LOGGER.info("Reached page_limit=%s, stopping extraction.", page_limit)
            break

    LOGGER.info("Extraction complete: %s page(s) uploaded to gs://%s/gutendex/%s/",
                page_num, bucket_name, run_ts)


def random_failure(rate):
    """Throws an error based on fail rate

    Args:
        rate: a float between 0 and 1
    """
    if rate < 0 or rate > 1:
        # Return without retrying the Job Task
        LOGGER.warning(
            f"Invalid FAIL_RATE env var value: {rate}. "
            + "Must be a float between 0 and 1 inclusive."
        )
        return

    sample = random.random()
    LOGGER.info(
        "Random failure check generated sample=%s against rate=%s",
        sample,
        rate,
    )
    if sample < rate:
        LOGGER.error("Task failure condition met: sample=%s < rate=%s", sample, rate)
        raise Exception("Task failed.")
    LOGGER.debug("Task passed failure simulation check")


# Start script
if __name__ == "__main__":
    LOGGER.info("Process started")
    CONFIG = load_config()
    LOGGER.info("Loaded %s config settings", len(CONFIG))
    try:
        main(SLEEP_MS, FAIL_RATE, config=CONFIG)
    except Exception as err:
        message = (
            f"Task #{TASK_INDEX}, " + f"Attempt #{TASK_ATTEMPT} failed: {str(err)}"
        )

        LOGGER.exception("Unhandled exception during task execution")
        print(json.dumps({"message": message, "severity": "ERROR"}))
        sys.exit(1)  # Retry Job Task by exiting the process
    LOGGER.info("Process finished")