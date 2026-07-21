import json
import logging
import os
import random
import sys
import time
import cowsay



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
def main(sleep_ms=0, fail_rate=0):
    """Program that simulates work using the sleep method and random failures.

    Args:
        sleep_ms: number of milliseconds to sleep
        fail_rate: rate of simulated errors
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
    random_failure(parse_float("FAIL_RATE", fail_rate, 0.0))

    duration_ms = int((time.time() - start_time) * 1000)
    LOGGER.info("Completed task successfully in %sms", duration_ms)


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
    try:
        main(SLEEP_MS, FAIL_RATE)
    except Exception as err:
        message = (
            f"Task #{TASK_INDEX}, " + f"Attempt #{TASK_ATTEMPT} failed: {str(err)}"
        )

        LOGGER.exception("Unhandled exception during task execution")
        print(json.dumps({"message": message, "severity": "ERROR"}))
        sys.exit(1)  # Retry Job Task by exiting the process
    LOGGER.info("Process finished")