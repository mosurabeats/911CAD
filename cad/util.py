import json
from datetime import datetime, timezone


def now_iso():
    """Current UTC time as ISO-8601 with a Z suffix (second precision)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value):
    """Parse an ISO-8601 timestamp; returns a normalized UTC string or raises ValueError."""
    if value is None or value == "":
        return None
    v = str(value).strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        # Naive timestamps are interpreted as server local time.
        dt = dt.astimezone()
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def jdump(obj):
    return json.dumps(obj, separators=(",", ":"), sort_keys=True, default=str)


def row_to_dict(row):
    return None if row is None else {k: row[k] for k in row.keys()}


def rows_to_list(rows):
    return [row_to_dict(r) for r in rows]
