"""Lambda entrypoint for the report-mailer function (runs outside VPC).

Triggered by S3 ObjectCreated events on reports/*.csv.
Downloads the report from S3 and sends it as an attachment via Amazon SES.
"""

from email.message import EmailMessage
import json
import logging
import os
from pathlib import Path
from urllib.parse import unquote_plus

import boto3

logger = logging.getLogger("report-mailer")
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


def handler(event, context):
    """Mailer Lambda handler."""
    # Log requirement from Lab 5 specification:
    logger.info("report-mailer triggered", extra={"trigger": "s3"})

    records = event.get("Records", [])
    if not records:
        logger.warning("No S3 records found in event.")
        return {"status": "no_records"}

    s3_info = records[0].get("s3", {})
    bucket = s3_info.get("bucket", {}).get("name")
    raw_key = s3_info.get("object", {}).get("key", "")
    key = unquote_plus(raw_key)

    if not bucket or not key:
        logger.error("Missing bucket or key in S3 record.")
        return {"status": "error", "message": "Missing bucket or key"}

    # Guard: only handle .csv files in reports/ prefix
    if not key.startswith("reports/") or not key.endswith(".csv"):
        logger.info("Ignoring object %s not matching reports/*.csv filter", key)
        return {"status": "ignored", "key": key}

    week = Path(key).stem

    from_email = os.getenv("REPORTS_FROM_EMAIL")
    to_email = os.getenv("REPORTS_TO_EMAIL")

    if not from_email or not to_email:
        raise ValueError("REPORTS_FROM_EMAIL and REPORTS_TO_EMAIL environment variables must be set")

    s3 = boto3.client("s3")
    logger.info("Fetching report from s3://%s/%s", bucket, key)
    s3_obj = s3.get_object(Bucket=bucket, Key=key)
    csv_bytes = s3_obj["Body"].read()

    # Build MIME email with attachment
    msg = EmailMessage()
    msg["Subject"] = f"Spry Weekly Meetings Report: {week}"
    msg["From"] = from_email
    msg["To"] = to_email
    msg.set_content(
        f"Hello,\n\n"
        f"Your automated Spry weekly meetings report for {week} is ready.\n"
        f"Please find the summary and breakdown attached as a CSV file.\n\n"
        f"— Spry Meeting Analytics Automation"
    )
    msg.add_attachment(
        csv_bytes,
        maintype="text",
        subtype="csv",
        filename=f"spry-weekly-report-{week}.csv",
    )

    region = os.getenv("AWS_REGION", "us-east-1")
    ses = boto3.client("sesv2", region_name=region)
    logger.info("Sending report for %s from %s to %s via SES", week, from_email, to_email)

    ses.send_email(
        FromEmailAddress=from_email,
        Destination={"ToAddresses": [to_email]},
        Content={"Raw": {"Data": msg.as_bytes()}},
    )

    logger.info("Email delivered to SES for %s", to_email)
    return {
        "status": "sent",
        "week": week,
        "recipient": to_email,
        "key": key,
    }
