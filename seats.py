"""Read-only BookMyShow seat-layout watch.

Uses the same GETSEATLAYOUT endpoint the public seat-layout page calls
(POST https://services-in.bookmyshow.com/doTrans.aspx). The response
strData is AES-CBC encrypted with the key the BMS web bundle ships to
every browser, which is how the page renders availability. This module
only decodes availability to alert; it never holds or books seats.
"""
from __future__ import annotations

import base64, json, os, time
from datetime import datetime
from zoneinfo import ZoneInfo

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

IST = ZoneInfo("Asia/Kolkata")

# Key is published in the BMS web JS bundle as SEAT_LAYOUT_ENCRYPTION_KEY.
SEAT_LAYOUT_KEY = b"kYp3s6v9y$B&E)H+MbQeThWmZq4t7w!z"
SEAT_LAYOUT_IV = b"\x00" * 16
SEAT_LAYOUT_URL = "https://services-in.bookmyshow.com/doTrans.aspx"

STATUS_NAMES = {
    0: "GANGWAY",
    1: "AVAILABLE",
    2: "BOOKED",
    3: "OTHER_CATEGORY",
    4: "BEST_SEAT",
    7: "HANDICAPPED",
    8: "COMPANION",
    9: "SOCIAL_DISTANCING",
}
AVAILABLE_STATUSES = {1, 4}

# Arjun's drawn PCX target: rows H-N, display seat numbers ~10 to ~40.
DUNE_PCX_ROWS = os.environ.get("DUNE_PCX_ROWS", "H-N")
DUNE_PCX_SEAT_MIN = int(os.environ.get("DUNE_PCX_SEAT_MIN", "10"))
DUNE_PCX_SEAT_MAX = int(os.environ.get("DUNE_PCX_SEAT_MAX", "40"))
SEAT_BLOCK_SIZE = int(os.environ.get("SEAT_BLOCK_SIZE", "3"))
# Equivalent zone for Allu Avengers 2D once a layout exists.
AVENGERS_BACK_FRAC = float(os.environ.get("AVENGERS_BACK_FRAC", "0.5"))
AVENGERS_CENTER_FRAC = float(os.environ.get("AVENGERS_CENTER_FRAC", "0.6"))
SEAT_ALERT_COOLDOWN_SEC = int(os.environ.get("SEAT_ALERT_COOLDOWN_SEC", str(15 * 60)))


def parse_row_range(spec):
    spec = (spec or "").strip().upper()
    if "-" in spec:
        a, b = spec.split("-", 1)
        start, end = a.strip(), b.strip()
        if len(start) == 1 and len(end) == 1 and start.isalpha() and end.isalpha():
            lo, hi = ord(start), ord(end)
            if lo > hi:
                lo, hi = hi, lo
            return [chr(i) for i in range(lo, hi + 1)]
    return [p.strip() for p in spec.split(",") if p.strip()]


DUNE_PCX_ROW_LIST = parse_row_range(DUNE_PCX_ROWS)


def decrypt_strdata(str_data):
    raw = base64.b64decode(str_data)
    cipher = AES.new(SEAT_LAYOUT_KEY, AES.MODE_CBC, SEAT_LAYOUT_IV)
    return unpad(cipher.decrypt(raw), 16).decode("utf-8", "replace")


def parse_layout(plain):
    parts = plain.split("||")
    if len(parts) < 2:
        return {"areas": [], "rows": []}
    areas = []
    for chunk in parts[0].split("|"):
        if not chunk:
            continue
        t = chunk.split(":")
        areas.append({
            "areaDescription": t[0] if t else "",
            "areaId": t[1] if len(t) > 1 else "",
            "areaCode": t[2] if len(t) > 2 else "",
        })
    rows = []
    for chunk in parts[1].split("|"):
        if not chunk:
            continue
        t = chunk.split(":")
        row_id = t[0] if t else ""
        row_num = t[1] if len(t) > 1 else ""
        seats = []
        for e in t[2:]:
            bits = e.split("+")
            code = bits[0] if bits else ""
            cinema = bits[1] if len(bits) > 1 else ""
            if not code:
                continue
            status = int(code[1]) if len(code) > 1 and code[1].isdigit() else None
            seats.append({
                "status": status,
                "status_name": STATUS_NAMES.get(status, f"UNKNOWN_{status}"),
                "seat_number": code[2:] if len(code) > 2 else "",
                "cinema_seat": None if cinema in ("", "0", "00") else cinema,
            })
        rows.append({"row_id": row_id, "row_number": row_num, "seats": seats})
    return {"areas": areas, "rows": rows}


def display_num(seat):
    raw = seat.get("cinema_seat") or seat.get("seat_number")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def row_letter(row):
    return (row.get("row_number") or row.get("row_id") or "").strip().upper()


def equivalent_zone(layout, back_frac=None, center_frac=None):
    """Back ~half of rows (A is front), central ~center_frac of seat numbers."""
    back_frac = AVENGERS_BACK_FRAC if back_frac is None else back_frac
    center_frac = AVENGERS_CENTER_FRAC if center_frac is None else center_frac
    letters = []
    seen = set()
    nums = []
    for row in layout.get("rows") or []:
        letter = row_letter(row)
        if letter and letter not in seen and any(s.get("status") != 0 for s in row.get("seats") or []):
            seen.add(letter)
            letters.append(letter)
        for s in row.get("seats") or []:
            n = display_num(s)
            if n is not None and s.get("status") != 0:
                nums.append(n)
    letters_sorted = sorted(letters, key=lambda x: (len(x), x))
    if not letters_sorted:
        return {"rows": [], "seat_min": None, "seat_max": None, "label": "empty-layout"}
    start = int(len(letters_sorted) * (1.0 - back_frac))
    rows = letters_sorted[start:]
    if nums:
        lo, hi = min(nums), max(nums)
        margin = (1.0 - center_frac) / 2.0 * (hi - lo)
        seat_min = int(round(lo + margin))
        seat_max = int(round(hi - margin))
    else:
        seat_min = seat_max = None
    label = f"rows {rows[0]}-{rows[-1]}, seats {seat_min}-{seat_max}"
    return {"rows": rows, "seat_min": seat_min, "seat_max": seat_max, "label": label}


def dune_pcx_zone():
    rows = list(DUNE_PCX_ROW_LIST)
    return {
        "rows": rows,
        "seat_min": DUNE_PCX_SEAT_MIN,
        "seat_max": DUNE_PCX_SEAT_MAX,
        "label": f"rows {rows[0]}-{rows[-1]}, seats {DUNE_PCX_SEAT_MIN}-{DUNE_PCX_SEAT_MAX}",
    }


def find_blocks(layout, zone, need=None):
    need = SEAT_BLOCK_SIZE if need is None else need
    allowed = set(zone.get("rows") or [])
    seat_lo, seat_hi = zone.get("seat_min"), zone.get("seat_max")
    blocks = []
    for row in layout.get("rows") or []:
        letter = row_letter(row)
        if letter not in allowed:
            continue
        run = []

        def flush():
            if len(run) >= need:
                seats = [str(n) for n in run]
                blocks.append({
                    "row": letter,
                    "seats": seats,
                    "count": len(seats),
                    "block_id": f"{letter}:{'-'.join(seats)}",
                })
            run.clear()

        for s in row.get("seats") or []:
            if s.get("status") == 0:
                flush()
                continue
            n = display_num(s)
            ok = (
                s.get("status") in AVAILABLE_STATUSES
                and n is not None
                and (seat_lo is None or n >= seat_lo)
                and (seat_hi is None or n <= seat_hi)
            )
            if not ok:
                flush()
                continue
            if run and abs(run[-1] - n) != 1:
                flush()
            run.append(n)
        flush()
    return blocks


def layout_summary(layout, zone=None):
    out = []
    for row in layout.get("rows") or []:
        letter = row_letter(row)
        avail, booked = [], 0
        for s in row.get("seats") or []:
            if s.get("status") == 0:
                continue
            n = display_num(s)
            if s.get("status") in AVAILABLE_STATUSES:
                avail.append(n)
            elif s.get("status") == 2:
                booked += 1
        rec = {"row": letter, "available": avail, "booked_count": booked}
        if zone and letter in set(zone.get("rows") or []):
            rec["in_zone"] = True
        out.append(rec)
    return out


def trans_form(venue, session_id):
    return {
        "strAppCode": "WEBV2",
        "lngTransactionIdentifier": "0",
        "strCommand": "GETSEATLAYOUT",
        "strVenueCode": venue,
        "strParam1": str(session_id),
        "strParam2": "WEB",
        "strParam3": "",
        "strParam4": "",
        "strParam5": "Y",
        "strParam6": "",
        "strParam7": "",
        "strParam8": "",
        "strParam9": "",
        "strParam10": "",
        "strFormat": "json",
    }


def trans_headers(venue, session_id, event_code, date_code):
    return {
        "Accept": "application/json, text/plain, */*",
        "Referer": f"https://in.bookmyshow.com/movies/hyd/seat-layout/{event_code}/{venue}/{session_id}/{date_code}",
        "Origin": "https://in.bookmyshow.com",
        "x-app-code": "WEBV2",
    }


def decode_layout_payload(payload):
    if isinstance(payload, str):
        payload = json.loads(payload)
    bms = payload.get("BookMyShow") if isinstance(payload, dict) else None
    if not bms:
        # maybe the JSON is already the wrapper
        if isinstance(payload, dict) and payload.get("strData"):
            bms = payload
        else:
            raise ValueError("no BookMyShow object")
    if str(bms.get("blnSuccess", "")).lower() != "true":
        raise ValueError(bms.get("strException") or "GETSEATLAYOUT failed")
    raw = bms.get("strData") or ""
    if not raw:
        raise ValueError("empty strData")
    return parse_layout(decrypt_strdata(raw))


def is_pcx_show(show):
    screen = " ".join([
        str(show.get("screen") or ""),
        str(show.get("format") or ""),
        str(show.get("attributes") or ""),
    ]).lower()
    return "pcx" in screen


def show_datetime(date_code, show_time):
    """Parse YYYYMMDD + '11:05 AM' into an IST datetime, or None."""
    if not date_code or not show_time:
        return None
    try:
        day = datetime.strptime(str(date_code), "%Y%m%d").date()
        t = datetime.strptime(str(show_time).strip(), "%I:%M %p").time()
        return datetime.combine(day, t, tzinfo=IST)
    except Exception:
        return None


def past_showtime(date_code, show_time, now=None):
    when = show_datetime(date_code, show_time)
    if when is None:
        return False
    return (now or datetime.now(IST)) >= when


def block_ids(blocks):
    return [b["block_id"] for b in blocks]


def should_alert(new_blocks, prev_record, now=None, cooldown=None):
    """Re-alert only if there is a block we have not already alerted, and cooldown has passed."""
    cooldown = SEAT_ALERT_COOLDOWN_SEC if cooldown is None else cooldown
    now = now or datetime.now(IST)
    ids = set(block_ids(new_blocks))
    if not ids:
        return False, []
    prev_ids = set((prev_record or {}).get("block_ids") or [])
    fresh = [b for b in new_blocks if b["block_id"] not in prev_ids]
    if not fresh:
        return False, []
    last = (prev_record or {}).get("last_alert_at")
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=IST)
            if (now - last_dt).total_seconds() < cooldown:
                return False, fresh
        except Exception:
            pass
    return True, fresh
