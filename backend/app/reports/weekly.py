"""Weekly meetings report generator for Spry Meeting Analytics."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timedelta, timezone
import io
import re

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db import SessionFactory
from app.models.meeting import Meeting


def parse_iso_week(week_str: str) -> tuple[datetime, datetime]:
    """Parse an ISO week string like '2026-W40' into UTC start and end bounds."""
    match = re.match(r"^(\d{4})-W(\d{1,2})$", week_str.strip())
    if not match:
        raise ValueError(f"Invalid ISO week format '{week_str}'. Expected 'YYYY-Www' (e.g. '2026-W40').")

    year, week_num = int(match.group(1)), int(match.group(2))
    # Monday of the ISO week at 00:00:00 UTC
    start_dt = datetime.strptime(f"{year}-W{week_num:02d}-1", "%G-W%V-%u").replace(
        tzinfo=timezone.utc
    )
    end_dt = start_dt + timedelta(days=7)
    return start_dt, end_dt


async def _query_meetings_for_window(start_dt: datetime, end_dt: datetime) -> list[Meeting]:
    """Fetch all meetings starting within the specified datetime window."""
    async with SessionFactory() as session:
        query = (
            select(Meeting)
            .options(selectinload(Meeting.participants))
            .where(Meeting.starts_at >= start_dt, Meeting.starts_at < end_dt)
            .order_by(Meeting.starts_at.asc())
        )
        result = await session.scalars(query)
        return list(result)


async def _generate_report_csv_async(week: str) -> bytes:
    """Async implementation of weekly report computation."""
    curr_start, curr_end = parse_iso_week(week)
    prev_start = curr_start - timedelta(days=7)
    prev_end = curr_start

    curr_meetings = await _query_meetings_for_window(curr_start, curr_end)
    prev_meetings = await _query_meetings_for_window(prev_start, prev_end)

    # Calculate metrics for current week
    curr_count = len(curr_meetings)
    curr_duration_sec = sum(
        max(0.0, (m.ends_at - m.starts_at).total_seconds()) for m in curr_meetings
    )
    curr_duration_hours = curr_duration_sec / 3600.0

    # Calculate metrics for previous week
    prev_count = len(prev_meetings)
    prev_duration_sec = sum(
        max(0.0, (m.ends_at - m.starts_at).total_seconds()) for m in prev_meetings
    )
    prev_duration_hours = prev_duration_sec / 3600.0

    # Week-over-week changes
    count_change = curr_count - prev_count
    duration_change_hours = curr_duration_hours - prev_duration_hours

    # Top 5 longest meetings
    meetings_with_duration = []
    for m in curr_meetings:
        duration_min = max(0.0, (m.ends_at - m.starts_at).total_seconds() / 60.0)
        meetings_with_duration.append((m, duration_min))

    top_longest = sorted(meetings_with_duration, key=lambda x: x[1], reverse=True)[:5]

    # Breakdown per organiser / owner
    owner_stats: dict[str, dict[str, float]] = {}
    for m in curr_meetings:
        owner = m.owner_id or "Unassigned"
        dur_hrs = max(0.0, (m.ends_at - m.starts_at).total_seconds() / 3600.0)
        if owner not in owner_stats:
            owner_stats[owner] = {"count": 0, "duration_hours": 0.0}
        owner_stats[owner]["count"] += 1
        owner_stats[owner]["duration_hours"] += dur_hrs

    # Write out CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["# Spry Weekly Meetings Report", f"Week: {week}"])
    writer.writerow(["Generated At (UTC)", datetime.now(timezone.utc).isoformat()])
    writer.writerow([])

    # Section 1: Executive Summary
    writer.writerow(["--- Executive Summary ---"])
    writer.writerow(["Metric", f"Current Week ({week})", "Previous Week", "Change"])
    writer.writerow(["Total Meetings Count", curr_count, prev_count, f"{count_change:+d}"])
    writer.writerow([
        "Total Meetings Duration (hours)",
        f"{curr_duration_hours:.2f}",
        f"{prev_duration_hours:.2f}",
        f"{duration_change_hours:+.2f}",
    ])
    writer.writerow([])

    # Section 2: Top 5 Longest Meetings
    writer.writerow(["--- Top 5 Longest Meetings ---"])
    writer.writerow(["Title", "Starts At (UTC)", "Duration (minutes)", "Participants Count", "Owner / Organizer"])
    if top_longest:
        for m, dur_min in top_longest:
            participants_count = len(m.participants) if m.participants else 0
            writer.writerow([
                m.name,
                m.starts_at.isoformat(),
                f"{dur_min:.1f}",
                participants_count,
                m.owner_id or "Unassigned",
            ])
    else:
        writer.writerow(["No meetings recorded this week", "", "", "", ""])
    writer.writerow([])

    # Section 3: Breakdown by Organizer
    writer.writerow(["--- Breakdown by Organizer ---"])
    writer.writerow(["Organizer / Owner ID", "Meetings Count", "Total Duration (hours)"])
    if owner_stats:
        for owner, stats in sorted(owner_stats.items(), key=lambda x: x[1]["duration_hours"], reverse=True):
            writer.writerow([owner, int(stats["count"]), f"{stats['duration_hours']:.2f}"])
    else:
        writer.writerow(["No meetings recorded", "", ""])

    return output.getvalue().encode("utf-8")


def build_weekly_report(week: str) -> bytes:
    """Query the meetings of one ISO week (e.g. '2026-W40') and return CSV bytes.

    Idempotent: The same week always generates reproducible report structure.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _generate_report_csv_async(week)).result()

    return asyncio.run(_generate_report_csv_async(week))
