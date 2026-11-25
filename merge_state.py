"""Merge incoming fired-dates.json with the watch-state copy (union, never wipe)."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo


def load(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def later(a, b):
    vals = [x for x in (a, b) if x]
    if not vals:
        return None

    def key(s):
        try:
            return datetime.fromisoformat(s)
        except Exception:
            return datetime.min

    return max(vals, key=key)


def merge(incoming, existing):
    pairs = set()
    for blob in (existing, incoming):
        for p in blob.get("fired_pairs") or []:
            if p:
                pairs.add(str(p))
    alerts = {}
    for blob in (existing, incoming):
        raw = blob.get("seat_alerts") or {}
        if not isinstance(raw, dict):
            continue
        for k, rec in raw.items():
            if not isinstance(rec, dict):
                continue
            prev = alerts.get(k) or {}
            ids = set(prev.get("block_ids") or []) | set(rec.get("block_ids") or [])
            alerts[k] = {
                "block_ids": sorted(ids),
                "last_alert_at": later(prev.get("last_alert_at"), rec.get("last_alert_at")),
                "zone": rec.get("zone") or prev.get("zone"),
            }
    return {
        "fired_pairs": sorted(pairs),
        "seat_alerts": alerts,
        "updated": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(timespec="seconds"),
    }


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2:
        print("usage: merge_state.py incoming.json existing.json", file=sys.stderr)
        return 2
    incoming_path, existing_path = argv
    merged = merge(load(incoming_path), load(existing_path))
    with open(incoming_path, "w") as f:
        json.dump(merged, f)
        f.write("\n")
    print("merged state:", json.dumps(merged))
    return 0


if __name__ == "__main__":
    sys.exit(main())
