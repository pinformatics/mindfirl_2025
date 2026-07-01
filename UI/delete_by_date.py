"""Delete Redis submissions between a start date and now.

Usage:
    python delete_by_date.py --dry-run
    python delete_by_date.py
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import redis as redis_lib

# ── config ───────────────────────────────────────────────────────────────────
REDIS_URL   = os.environ.get("REDIS_URL", "redis://localhost:6379")
DATA_PATH   = "data/ppirl_priv.csv"          # must match ui_constants.py
START_DATE  = datetime(2026, 5, 1, tzinfo=timezone.utc)   # inclusive
# END_DATE defaults to now (no upper bound needed)
# ─────────────────────────────────────────────────────────────────────────────

def parse_saved_at(raw):
    if not raw:
        return None
    s = str(raw).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None

def main(dry_run):
    r = redis_lib.from_url(REDIS_URL, decode_responses=True)

    pattern = f"id:*___file:{DATA_PATH}"
    response_keys = list(r.scan_iter(pattern))
    print(f"Found {len(response_keys)} total response keys for {DATA_PATH}")

    to_delete = []
    skipped_no_snapshot = []
    skipped_out_of_range = []

    for rk in sorted(response_keys):
        sk = f"{rk}___snapshot"
        raw_snapshot = r.get(sk)
        if not raw_snapshot:
            skipped_no_snapshot.append(rk)
            continue
        try:
            snap = json.loads(raw_snapshot)
        except json.JSONDecodeError:
            skipped_no_snapshot.append(rk)
            continue

        dt = parse_saved_at(snap.get("saved_at"))
        if dt is None or dt < START_DATE:
            skipped_out_of_range.append(rk)
            continue

        to_delete.append((rk, rk + "___snapshot", dt.isoformat()))

    print(f"\n  In range (>= {START_DATE.date()}): {len(to_delete)}")
    print(f"  Out of range / no date:            {len(skipped_out_of_range) + len(skipped_no_snapshot)}")
    print()

    for rk, sk, ts in to_delete:
        print(f"  {'[DRY RUN] would delete' if dry_run else 'DELETING'}: {rk}  (saved_at={ts})")
        if not dry_run:
            r.delete(rk)
            r.delete(sk)

    if dry_run:
        print(f"\nDry run complete. Re-run without --dry-run to delete {len(to_delete)} submission(s).")
    else:
        print(f"\nDeleted {len(to_delete)} submission(s).")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
