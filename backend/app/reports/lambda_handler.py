"""Lambda entrypoint for the report-builder function (runs inside VPC).

Triggered by:
1. EventBridge Scheduler (sends message to SQS or direct invocation).
2. SQS message via on-demand rebuild (`make report-now WEEK=...`).
"""

from datetime import datetime, timedelta
import json
import logging
import os
from zoneinfo import ZoneInfo

import boto3

from app.config import settings
from app.reports.weekly import build_weekly_report

logger = logging.getLogger("report-builder")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        json.dumps({
            "time": "%(asctime)s",
            "level": "%(levelname)s",
            "name": "%(name)s",
            "message": "%(message)s",
            "trigger": "%(trigger)s",
        })
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)


def detect_trigger(event: dict) -> str:
    """Detect whether the invocation originated from schedule, SQS, or direct."""
    # Check if event is from SQS
    records = event.get("Records")
    if records and isinstance(records, list) and len(records) > 0:
        first_record = records[0]
        if first_record.get("eventSource") == "aws:sqs":
            try:
                body = json.loads(first_record.get("body", "{}"))
                if body.get("source") == "schedule":
                    return "schedule"
            except Exception:
                pass
            return "sqs"

    # Check direct invocation payload
    if event.get("source") == "schedule" or event.get("detail-type") == "Scheduled Event":
        return "schedule"

    return "direct"


def get_target_week(payload: dict) -> str:
    """Extract week from payload, or default to the previous ISO week."""
    week = payload.get("week")
    if week and isinstance(week, str) and week.strip():
        return week.strip()

    # Default to the previous ISO week in configured application timezone
    now = datetime.now(ZoneInfo(settings.app_timezone))
    last_week_dt = now - timedelta(days=7)
    return last_week_dt.strftime("%G-W%V")


def handler(event, context):
    """Builder Lambda handler."""
    trigger = detect_trigger(event)
    # Log requirement from Lab 5 specification:
    logger.info("report-builder triggered", extra={"trigger": trigger})

    # Extract payload from SQS record or direct invocation
    payload = event
    if event.get("Records") and isinstance(event["Records"], list):
        record = event["Records"][0]
        if record.get("eventSource") == "aws:sqs":
            try:
                payload = json.loads(record.get("body", "{}"))
            except Exception:
                payload = {}

    week = get_target_week(payload)
    logger.info("Generating weekly report for week: %s", week)

    # Build report CSV bytes
    csv_bytes = build_weekly_report(week)

    bucket = os.getenv("REPORTS_BUCKET")
    if not bucket:
        raise ValueError("REPORTS_BUCKET environment variable is not set")

    key = f"reports/{week}.csv"
    s3 = boto3.client("s3")
    logger.info("Uploading report to s3://%s/%s (%d bytes)", bucket, key, len(csv_bytes))

    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=csv_bytes,
        ContentType="text/csv",
    )

    logger.info("Report %s successfully uploaded to S3", key)
    return {
        "status": "success",
        "week": week,
        "bucket": bucket,
        "key": key,
        "bytes": len(csv_bytes),
        "trigger": trigger,
    }
