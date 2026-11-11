#!/usr/bin/env python3
"""GitHub Actions poller: detect a Ramayana Hindi ~15:00 show listed at Allu Cinemas Kokapet (ALUC)
on 14/15 Nov 2026 and POST the automations webhook once. Seats are not checked here."""
import argparse, json, os, random, sys, time, urllib.error, urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo
from curl_cffi.requests import Session

IST = ZoneInfo("Asia/Kolkata")
TARGET_DATES = ["20261114", "20261115"]
STOP_AT = datetime(2026, 11, 15, 15, 0, tzinfo=IST)
VENUE = "ALUC"
WEBHOOK_URL = "https://api2.cursor.sh/automations/webhook/a1fc7d82-93e8-575c-85ab-77aaea5f1cb0"


def now_ist():
    return datetime.now(IST)


def log(msg):
    print(f"{now_ist().isoformat(timespec='seconds')} {msg}", flush=True)


PROXY = os.environ.get("BMS_PROXY", "").strip()  # Cloudflare WARP socks proxy on the runner
LAST_ROUTE = [None]


def new_session():
    return Session(impersonate="chrome")


def _parse(text):
    """Accept raw JSON or the r.jina.ai wrapper (header lines, then the JSON body)."""
    i = text.find("{")
    if i < 0:
        raise ValueError("no JSON in body")
    return json.JSONDecoder().raw_decode(text[i:])[0]


def _get(session, route, url):
    if route == "warp":
        return session.get(url, timeout=20, proxies={"http": PROXY, "https": PROXY})
    if route == "jina":
        return session.get("https://r.jina.ai/" + url, timeout=40, headers={"x-no-cache": "true"})
    return session.get(url, timeout=20)


def fetch(session_box, date_code):
    """Routes: WARP proxy (BMS blocks GitHub/Azure IPs directly), then direct, then r.jina.ai relay."""
    url = f"https://in.bookmyshow.com/api/v2/mobile/showtimes/byvenue?venueCode={VENUE}&dateCode={date_code}"
    routes = (["warp"] if PROXY else []) + ["direct", "jina"]
    last, status = None, None
    for attempt in range(2):
        for route in routes:
            status = None
            try:
                resp = _get(session_box[0], route, url)
                status = resp.status_code
                if status != 200:
                    raise ValueError(f"HTTP {status}")
                data = _parse(resp.text or "")
                if "ShowDatesArray" not in data:
                    raise ValueError("unexpected JSON")
                LAST_ROUTE[0] = route
                return status, data
            except Exception as exc:
                last = f"route={route} status={status} {type(exc).__name__}: {str(exc)[:160]}"
                log(f"{date_code} fetch failed {last}")
                try:
                    session_box[0].close()
                except Exception:
                    pass
                session_box[0] = new_session()
        if attempt < 1:
            time.sleep(random.uniform(3, 8))
    return status, {"_error": last}


def hhmm(show_dt):
    s = str(show_dt)
    return int(s[-4:]) if len(s) >= 4 and s[-4:].isdigit() else None


def analyze(data, date_code):
    """Same rules as /workspace/bms-watch/watch.py analyze()."""
    dates = data.get("ShowDatesArray") or []
    open_flag = any(str(d.get("DateCode")) == date_code and d.get("isDisabled") is False for d in dates)
    details = data.get("ShowDetails") or []
    trusted = bool(details) and str(details[0].get("Date")) == date_code
    if not open_flag or not trusted:
        return {"open": False, "ramayana_hindi": 0, "matches": []}
    hindi, matches = 0, []
    for event in details[0].get("Event") or []:
        title = event.get("EventTitle") or ""
        if "ramayana" not in title.lower():
            continue
        for child in event.get("ChildEvents") or []:
            if child.get("EventLanguage") != "Hindi":
                continue
            hindi += len(child.get("ShowTimes") or [])
            dim = child.get("EventDimension") or ""
            for show in child.get("ShowTimes") or []:
                t = hhmm(show.get("ShowDateTime"))
                if t is None or t < 1430 or t > 1530:
                    continue
                attrs = show.get("Attributes") or ""
                code, sid = child.get("EventCode"), show.get("SessionId")
                matches.append({
                    "time": show.get("ShowTime"),
                    "show_datetime": show.get("ShowDateTime"),
                    "screen": show.get("ScreenName"),
                    "format": dim,
                    "attributes": attrs,
                    "is_dolby": "DOLBY" in dim.upper() or "DOLBY" in str(attrs).upper(),
                    "sessionId": sid,
                    "event_code": code,
                    "availability": show.get("Availability"),
                    "min_price": show.get("MinPrice"),
                    "max_price": show.get("MaxPrice"),
                    "seat_layout_url": f"https://in.bookmyshow.com/movies/hyd/seat-layout/{code}/{VENUE}/{sid}/{date_code}",
                })
    matches.sort(key=lambda m: (not m["is_dolby"], str(m.get("show_datetime") or "")))
    return {"open": True, "ramayana_hindi": hindi, "matches": matches}


def post_webhook(body, key, retries=4):
    data = json.dumps(body).encode()
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(WEBHOOK_URL, data=data, method="POST", headers={
                "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                log(f"webhook POST status={resp.getcode()} attempt={attempt}")
                if 200 <= resp.getcode() < 300:
                    return True
        except urllib.error.HTTPError as exc:
            log(f"webhook POST failed attempt={attempt} status={exc.code}")
        except Exception as exc:
            log(f"webhook POST failed attempt={attempt} {type(exc).__name__}")
        time.sleep(2 ** attempt)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="no POST; also fetch --extra dates and print counts")
    ap.add_argument("--extra", default="", help="comma-separated extra dates for test mode")
    ap.add_argument("--duration", type=float, default=270, help="seconds to keep polling")
    ap.add_argument("--interval", type=float, default=45)
    args = ap.parse_args()

    if now_ist() >= STOP_AT and not args.test:
        log("past Sun 15 Nov 2026 15:00 IST, nothing to do")
        return 0
    key = os.environ.get("WEBHOOK_KEY", "").strip()
    if not args.test and not key:
        log("WEBHOOK_KEY secret missing; refusing to run live")
        return 2

    sb = [new_session()]
    if args.test:
        dates = TARGET_DATES + [d for d in args.extra.split(",") if d]
        summary = {}
        for d in dates:
            status, data = fetch(sb, d)
            err = data.get("_error")
            a = analyze(data, d) if not err else {"open": False, "ramayana_hindi": 0, "matches": []}
            returned = None
            if not err:
                det = data.get("ShowDetails") or []
                returned = det[0].get("Date") if det else None
            summary[d] = {"status": status, "route": LAST_ROUTE[0] if not err else None, "error": err, "returned_date": returned, "open": a["open"],
                          "ramayana_hindi_shows": a["ramayana_hindi"], "matches_1430_1530": len(a["matches"]),
                          "match_times": [f'{m["time"]} {m["screen"]} {m["format"]}' for m in a["matches"]]}
            log(f"TEST {d}: {json.dumps(summary[d])}")
        print("TEST_SUMMARY " + json.dumps(summary))
        # exercise every route individually against the first date given via --extra (positive control)
        ctrl = ([d for d in args.extra.split(",") if d] or TARGET_DATES)[0]
        url = f"https://in.bookmyshow.com/api/v2/mobile/showtimes/byvenue?venueCode={VENUE}&dateCode={ctrl}"
        for route in (["warp"] if PROXY else []) + ["direct", "jina"]:
            try:
                resp = _get(new_session(), route, url)
                a = analyze(_parse(resp.text), ctrl) if resp.status_code == 200 else {"open": None, "matches": []}
                log(f"ROUTE {route} {ctrl}: status={resp.status_code} open={a['open']} matches={len(a['matches'])}")
            except Exception as exc:
                log(f"ROUTE {route} {ctrl}: {type(exc).__name__}: {str(exc)[:160]}")
        ok = all(v["status"] == 200 and not v["error"] for v in summary.values())
        return 0 if ok else 1

    end = time.monotonic() + args.duration
    errors = 0
    while True:
        if now_ist() >= STOP_AT:
            log("reached stop time")
            return 0
        for d in TARGET_DATES:
            status, data = fetch(sb, d)
            if data.get("_error"):
                errors += 1
                log(f"{d} fetch error {data['_error']}")
                continue
            a = analyze(data, d)
            log(f"{d} status={status} route={LAST_ROUTE[0]} open={a['open']} ramayana_hindi={a['ramayana_hindi']} matches={len(a['matches'])}")
            if a["matches"]:
                body = {"source": "github", "event": "ramayana_show_listed", "venue": VENUE, "date": d,
                        "detected_at": now_ist().isoformat(timespec="seconds"), "shows": a["matches"]}
                log("MATCH " + json.dumps(body))
                if post_webhook(body, key):
                    with open(os.environ.get("GITHUB_OUTPUT", "/dev/null"), "a") as f:
                        f.write("posted=true\n")
                    return 0
                log("webhook POST failed after retries; will retry next loop")
        if time.monotonic() + args.interval > end:
            break
        time.sleep(args.interval + random.uniform(-5, 5))
    return 1 if errors >= 6 else 0


if __name__ == "__main__":
    sys.exit(main())
