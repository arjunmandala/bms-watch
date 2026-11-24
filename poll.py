#!/usr/bin/env python3
"""GitHub Actions poller: detect English Dune: Part Three shows at Allu Cinemas
Kokapet (ALUC) on Sat 19 / Sun 20 Dec 2026 and POST the automations webhook
once per date. Any time, any format. Seats are not checked here."""
import argparse, json, os, random, sys, time, urllib.error, urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo
from curl_cffi.requests import Session

IST = ZoneInfo("Asia/Kolkata")
TARGET_DATES = ["20261219", "20261220"]
STOP_AT = datetime(2026, 12, 20, 23, 59, tzinfo=IST)
VENUE = "ALUC"
WEBHOOK_URL = "https://api2.cursor.sh/automations/webhook/17ae01ad-cb53-52d5-adcb-07efca91ef28"
STATE_PATH = os.environ.get("FIRED_STATE_PATH", ".fired-dates.json")
EXAMPLE_PAYLOAD = {
    "source": "github",
    "event": "dune_show_listed",
    "venue": "ALUC",
    "date": "20261219",
    "shows": [{
        "time": "07:45 PM",
        "screen": "SCREEN 1",
        "format": "DOLBY CINEMA",
        "language": "English",
        "availability": "A",
        "min_price": "395.00",
        "max_price": "450.00",
        "session_id": "0000",
        "event_code": "ET00000000",
    }],
}


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


def is_english(lang):
    return (lang or "").strip().lower().startswith("english")


def is_dune_title(title):
    return title_matches(title, "dune")


def title_matches(title, needle):
    return (needle or "").lower() in (title or "").lower()


def show_payload(child, show):
    return {
        "time": show.get("ShowTime"),
        "screen": show.get("ScreenName"),
        "format": child.get("EventDimension") or show.get("Attributes") or "",
        "language": child.get("EventLanguage") or "",
        "availability": show.get("Availability"),
        "min_price": show.get("MinPrice"),
        "max_price": show.get("MaxPrice"),
        "session_id": show.get("SessionId"),
        "event_code": child.get("EventCode"),
    }


def build_payload(date_code, shows, **extra):
    body = {
        "source": "github",
        "event": "dune_show_listed",
        "venue": VENUE,
        "date": date_code,
        "shows": shows,
    }
    body.update(extra)
    return body


def analyze(data, date_code, title_contains="dune"):
    """Open date + EventTitle containing title_contains + English child events, any time/format."""
    dates = data.get("ShowDatesArray") or []
    open_flag = any(str(d.get("DateCode")) == date_code and d.get("isDisabled") is False for d in dates)
    details = data.get("ShowDetails") or []
    trusted = bool(details) and str(details[0].get("Date")) == date_code
    if not open_flag or not trusted:
        return {"open": False, "dune_english": 0, "matches": []}
    english, matches = 0, []
    for event in details[0].get("Event") or []:
        title = event.get("EventTitle") or ""
        if not title_matches(title, title_contains):
            # Child EventName can differ; still require the parent title to match.
            continue
        for child in event.get("ChildEvents") or []:
            lang = child.get("EventLanguage") or ""
            if not is_english(lang):
                continue
            shows = child.get("ShowTimes") or []
            english += len(shows)
            for show in shows:
                matches.append(show_payload(child, show))
    matches.sort(key=lambda m: (str(m.get("time") or ""), str(m.get("screen") or ""), str(m.get("format") or "")))
    return {"open": True, "dune_english": english, "matches": matches}


def load_fired():
    fired = set()
    extra = os.environ.get("FIRED_DATES", "").strip()
    if extra:
        fired.update(d.strip() for d in extra.split(",") if d.strip())
    try:
        with open(STATE_PATH) as f:
            data = json.load(f)
        fired.update(str(d) for d in (data.get("fired_dates") or []))
    except FileNotFoundError:
        pass
    except Exception as exc:
        log(f"could not read {STATE_PATH}: {type(exc).__name__}")
    return fired


def save_fired(fired):
    payload = {"fired_dates": sorted(fired), "updated": now_ist().isoformat(timespec="seconds")}
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f)
        f.write("\n")
    os.replace(tmp, STATE_PATH)
    log(f"saved state {STATE_PATH} fired_dates={sorted(fired)}")


def write_output(**kwargs):
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a") as f:
        for k, v in kwargs.items():
            f.write(f"{k}={v}\n")


def post_webhook(body, key, retries=4):
    """POST the webhook. Returns (ok, http_status_or_None). Does not retry 401/403."""
    data = json.dumps(body).encode()
    last_status = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(WEBHOOK_URL, data=data, method="POST", headers={
                "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                last_status = resp.getcode()
                log(f"webhook POST status={last_status} attempt={attempt}")
                if 200 <= last_status < 300:
                    return True, last_status
        except urllib.error.HTTPError as exc:
            last_status = exc.code
            log(f"webhook POST failed attempt={attempt} status={last_status}")
            if last_status in (401, 403):
                log("webhook rejected the key (401/403); not retrying")
                return False, last_status
        except Exception as exc:
            log(f"webhook POST failed attempt={attempt} {type(exc).__name__}")
        if attempt < retries:
            time.sleep(2 ** attempt)
    return False, last_status


DRILL_DATE = "20261219"
DRILL_TITLE = "Avengers: Doomsday"
DRILL_MOVIE = "Avengers: Doomsday"


def run_drill(session_box, key):
    """One-shot end-to-end alert drill. Never reads or writes fired_dates state."""
    log(f"DRILL start date={DRILL_DATE} movie={DRILL_MOVIE!r} (no state touch)")
    status, data = fetch(session_box, DRILL_DATE)
    if data.get("_error"):
        log(f"DRILL fetch error status={status} {data['_error']}")
        print("WEBHOOK_HTTP_STATUS none")
        write_output(webhook_status="none", drill_posted="false")
        return 1
    a = analyze(data, DRILL_DATE, title_contains=DRILL_TITLE)
    returned = None
    det = data.get("ShowDetails") or []
    if det:
        returned = det[0].get("Date")
    log(
        f"DRILL {DRILL_DATE} status={status} route={LAST_ROUTE[0]} "
        f"returned_date={returned} open={a['open']} matches={len(a['matches'])}"
    )
    if not a["matches"]:
        log("DRILL no English Avengers: Doomsday shows found; not posting")
        print("WEBHOOK_HTTP_STATUS none")
        write_output(webhook_status="none", drill_posted="false")
        return 1
    body = build_payload(DRILL_DATE, a["matches"], drill=True, movie=DRILL_MOVIE)
    log("DRILL PAYLOAD " + json.dumps(body))
    ok, http_status = post_webhook(body, key, retries=4)
    printed = "none" if http_status is None else str(http_status)
    print(f"WEBHOOK_HTTP_STATUS {printed}")
    log(f"DRILL webhook status={printed} ok={ok}")
    write_output(webhook_status=printed, drill_posted="true" if ok else "false")
    if http_status in (401, 403):
        log("DRILL: webhook rejected WEBHOOK_KEY (wrong or stale key); not retrying with any other secret")
        return 1
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="no POST; fetch dates and print would-be payloads")
    ap.add_argument("--drill", action="store_true", help="POST a real drill alert for Avengers: Doomsday on 20261219; does not touch fired_dates")
    ap.add_argument("--extra", default="", help="comma-separated extra dates for test mode")
    ap.add_argument("--duration", type=float, default=270, help="seconds to keep polling")
    ap.add_argument("--interval", type=float, default=45)
    args = ap.parse_args()

    if now_ist() >= STOP_AT and not args.test and not args.drill:
        log("past Sun 20 Dec 2026 23:59 IST, nothing to do")
        write_output(all_fired="true")
        return 0
    key = os.environ.get("WEBHOOK_KEY", "").strip()
    if not args.test and not key:
        log("WEBHOOK_KEY secret missing; refusing to run live or drill")
        return 2

    sb = [new_session()]
    if args.drill:
        return run_drill(sb, key)
    if args.test:
        dates = TARGET_DATES + [d for d in args.extra.split(",") if d]
        summary = {}
        would = []
        for d in dates:
            status, data = fetch(sb, d)
            err = data.get("_error")
            a = analyze(data, d) if not err else {"open": False, "dune_english": 0, "matches": []}
            returned = None
            if not err:
                det = data.get("ShowDetails") or []
                returned = det[0].get("Date") if det else None
            summary[d] = {
                "status": status,
                "route": LAST_ROUTE[0] if not err else None,
                "error": err,
                "returned_date": returned,
                "open": a["open"],
                "dune_english_shows": a["dune_english"],
                "matches": len(a["matches"]),
                "match_times": [f'{m["time"]} {m["screen"]} {m["format"]} {m["language"]}' for m in a["matches"]],
            }
            log(f"TEST {d}: {json.dumps(summary[d])}")
            if a["matches"]:
                body = build_payload(d, a["matches"])
                would.append(body)
                log("WOULD_BE_PAYLOAD " + json.dumps(body))
        print("TEST_SUMMARY " + json.dumps(summary))
        print("WOULD_BE_PAYLOADS " + json.dumps(would))
        if not would:
            log("no English Dune shows yet; no webhook would be posted")
            print("WOULD_BE_PAYLOAD_EXAMPLE " + json.dumps(EXAMPLE_PAYLOAD))
        # exercise every route individually against the first date given via --extra (positive control)
        ctrl = ([d for d in args.extra.split(",") if d] or TARGET_DATES)[0]
        url = f"https://in.bookmyshow.com/api/v2/mobile/showtimes/byvenue?venueCode={VENUE}&dateCode={ctrl}"
        for route in (["warp"] if PROXY else []) + ["direct", "jina"]:
            try:
                resp = _get(new_session(), route, url)
                a = analyze(_parse(resp.text), ctrl) if resp.status_code == 200 else {"open": None, "matches": []}
                log(f"ROUTE {route} {ctrl}: status={resp.status_code} open={a.get('open')} matches={len(a.get('matches') or [])}")
            except Exception as exc:
                log(f"ROUTE {route} {ctrl}: {type(exc).__name__}: {str(exc)[:160]}")
        ok = all(v["status"] == 200 and not v["error"] for v in summary.values())
        return 0 if ok else 1

    fired = load_fired()
    log(f"watching {TARGET_DATES}; already fired={sorted(fired)}")
    if set(TARGET_DATES) <= fired:
        log("all watched dates already fired")
        write_output(all_fired="true")
        return 0

    end = time.monotonic() + args.duration
    errors = 0
    while True:
        if now_ist() >= STOP_AT:
            log("reached stop time")
            write_output(all_fired="true" if set(TARGET_DATES) <= fired else "false")
            return 0
        for d in TARGET_DATES:
            if d in fired:
                continue
            status, data = fetch(sb, d)
            if data.get("_error"):
                errors += 1
                log(f"{d} fetch error {data['_error']}")
                continue
            a = analyze(data, d)
            log(f"{d} status={status} route={LAST_ROUTE[0]} open={a['open']} dune_english={a['dune_english']} matches={len(a['matches'])}")
            if not a["matches"]:
                continue
            body = build_payload(d, a["matches"])
            log("MATCH " + json.dumps(body))
            ok, _status = post_webhook(body, key)
            if ok:
                fired.add(d)
                save_fired(fired)
                write_output(posted="true", posted_date=d)
                if set(TARGET_DATES) <= fired:
                    log("all watched dates have fired")
                    write_output(all_fired="true")
                    return 0
                log(f"still watching {[x for x in TARGET_DATES if x not in fired]}")
            else:
                log("webhook POST failed after retries; will retry next loop")
        if time.monotonic() + args.interval > end:
            break
        time.sleep(args.interval + random.uniform(-5, 5))
    write_output(all_fired="true" if set(TARGET_DATES) <= fired else "false")
    return 1 if errors >= 6 else 0


if __name__ == "__main__":
    sys.exit(main())
