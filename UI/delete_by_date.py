"""Delete Redis submissions in a date range, across one, several, or all tracks.

Usage:
    python delete_by_date.py --start 2026-07-01                       # dry run, all tracks
    python delete_by_date.py --start 2026-07-01 --end 2026-07-08      # dry run, bounded range
    python delete_by_date.py --start 2026-07-01 --track mindfirl      # dry run, one track
    python delete_by_date.py --start 2026-07-01 --delete              # actually delete (asks to confirm)

Dates are UTC and inclusive; --start accepts YYYY-MM-DD or a full ISO
timestamp. --end defaults to now. Without --delete, nothing is ever removed
-- this always previews first. With --delete, you must additionally type
DELETE at a confirmation prompt before anything is actually removed.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import redis_factory
from ui_constants import LEGACY_DATA_PATH, TRACKS
from user_state import get_response_keys_for_track, get_snapshot_key_for_response_key, safe_parse_json

ALL_TRACK_KEYS = list(TRACKS.keys()) + ["legacy"]


def parse_saved_at(raw):
    """Parse a snapshot's saved_at timestamp as a UTC-aware datetime."""
    if not raw:
        return None
    text = str(raw).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_date_arg(raw, end_of_day=False):
    """Parse a --start/--end CLI argument (YYYY-MM-DD or full ISO) as UTC-aware."""
    text = raw.strip()
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        raise SystemExit(f"Invalid date {raw!r}. Use YYYY-MM-DD or a full ISO timestamp.")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if end_of_day and len(text) <= 10:
        # A bare date was given for --end; include the entire day.
        dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    return dt


def find_deletions(redis_client, track, start_date, end_date):
    """Return (matching (response_key, snapshot_key, saved_at) tuples, skipped count) for a track."""
    legacy_filename = LEGACY_DATA_PATH if track == "legacy" else None
    response_keys = get_response_keys_for_track(redis_client, track, legacy_filename=legacy_filename)

    matches = []
    skipped = 0
    for response_key in response_keys:
        snapshot_key = get_snapshot_key_for_response_key(response_key)
        snapshot = safe_parse_json(redis_client.get(snapshot_key), None)
        if not snapshot:
            skipped += 1
            continue

        saved_at = parse_saved_at(snapshot.get("saved_at"))
        if saved_at is None or saved_at < start_date or (end_date is not None and saved_at > end_date):
            skipped += 1
            continue

        matches.append((response_key, snapshot_key, saved_at.isoformat()))

    return matches, skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", required=True, help="Inclusive start date (YYYY-MM-DD or ISO timestamp), UTC.")
    parser.add_argument("--end", help="Inclusive end date (YYYY-MM-DD or ISO timestamp), UTC. Default: now.")
    parser.add_argument(
        "--track",
        choices=ALL_TRACK_KEYS + ["all"],
        default="all",
        help="Which track to target: {}, or 'all' (default) for every track.".format(", ".join(ALL_TRACK_KEYS)),
    )
    parser.add_argument("--delete", action="store_true", help="Actually delete. Without this flag, runs as a dry run (preview only).")
    args = parser.parse_args()

    start_date = parse_date_arg(args.start)
    end_date = parse_date_arg(args.end, end_of_day=True) if args.end else None

    tracks_to_scan = ALL_TRACK_KEYS if args.track == "all" else [args.track]

    r = redis_factory.create_redis_client()

    all_matches = []
    for track in tracks_to_scan:
        matches, skipped = find_deletions(r, track, start_date, end_date)
        print(f"[{track}] {len(matches)} in range, {skipped} skipped (no snapshot / out of range)")
        for response_key, snapshot_key, saved_at in matches:
            print(f"    match: {response_key}  (saved_at={saved_at})")
            all_matches.append((track, response_key, snapshot_key))

    range_desc = "{} to {}".format(start_date.date(), end_date.date() if end_date else "now")
    scope_desc = "all tracks" if args.track == "all" else args.track
    print(f"\nTotal matching submissions across {scope_desc} in range {range_desc}: {len(all_matches)}")

    if not args.delete:
        print("\nDry run complete. Re-run with --delete to remove these submissions.")
        return

    if not all_matches:
        print("\nNothing to delete.")
        return

    print(f"\nThis will PERMANENTLY delete {len(all_matches)} submission(s) (response + snapshot keys) from Redis.")
    print("This cannot be undone.")
    confirmation = input("Type DELETE (all caps) to confirm, anything else to abort: ").strip()
    if confirmation != "DELETE":
        print("Confirmation not received. Aborting -- nothing deleted.")
        return

    deleted = 0
    for track, response_key, snapshot_key in all_matches:
        r.delete(response_key)
        r.delete(snapshot_key)
        deleted += 1

    print(f"\nDeleted {deleted} submission(s).")


if __name__ == "__main__":
    main()
