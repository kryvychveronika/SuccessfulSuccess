"""Unit tests for the weekly reports module and trigger detection."""

from datetime import datetime, timezone
import pytest

from app.reports.weekly import parse_iso_week
from app.reports.lambda_handler import detect_trigger, get_target_week


def test_parse_iso_week_valid():
    start, end = parse_iso_week("2026-W40")
    assert start.year == 2026
    assert start.tzinfo == timezone.utc
    # 2026-W40 Monday is 2026-09-28
    assert start.strftime("%Y-%m-%d") == "2026-09-28"
    assert end.strftime("%Y-%m-%d") == "2026-10-05"
    assert (end - start).days == 7


def test_parse_iso_week_invalid():
    with pytest.raises(ValueError, match="Invalid ISO week format"):
        parse_iso_week("invalid-week")


def test_detect_trigger():
    # SQS event
    sqs_event = {
        "Records": [{
            "eventSource": "aws:sqs",
            "body": '{"week": "2026-W40", "source": "sqs"}'
        }]
    }
    assert detect_trigger(sqs_event) == "sqs"

    # Schedule event through SQS
    sched_sqs_event = {
        "Records": [{
            "eventSource": "aws:sqs",
            "body": '{"week": null, "source": "schedule"}'
        }]
    }
    assert detect_trigger(sched_sqs_event) == "schedule"

    # Direct schedule invocation
    direct_sched = {"source": "schedule"}
    assert detect_trigger(direct_sched) == "schedule"

    # Direct manual invocation
    direct = {"week": "2026-W40"}
    assert detect_trigger(direct) == "direct"


def test_get_target_week():
    assert get_target_week({"week": "2026-W39"}) == "2026-W39"
    # When week is null or empty, returns a valid ISO week format like YYYY-Www
    default_week = get_target_week({})
    assert "-W" in default_week
