#!/usr/bin/env python3
"""GitHub Actions poller for two BookMyShow targets on Sat 19 / Sun 20 Dec 2026:

1. English Dune: Part Three at Prasads Multiplex Hyderabad (PRHN), any format.
   Screen name (plus attributes) distinguishes the PCX screen from the others.
2. English Avengers: Doomsday at Allu Cinemas Kokapet (ALUC), 2D only
   (format / attributes must not contain '3D').

Fires the automations webhook once per (movie, date). Also watches seat
maps for English Dune on the Prasads PCX screen and, once listed, English
2D Avengers at Allu, alerting on 3 adjacent seats in a configured zone.
"""
import argparse, json, os, random, sys, time, urllib.error, urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo
from curl_cffi.requests import Session
import seats

IST = ZoneInfo("Asia/Kolkata")
STOP_AT = datetime(2026, 12, 20, 23, 59, tzinfo=IST)
WEBHOOK_URL = "https://api2.cursor.sh/automations/webhook/17ae01ad-cb53-52d5-adcb-07efca91ef28"
STATE_PATH = os.environ.get("FIRED_STATE_PATH", ".fired-dates.json")

# id is the state key. movie is the payload label.
TARGETS = [
    {
        "id": "dune",
        "movie": "Dune: Part Three",
        "title_contains": "dune",
        "venue": "PRHN",
        "dates": ["20261219", "20261220"],
        "format": "any",
    },
    {
        "id": "avengers",
        "movie": "Avengers: Doomsday",
        "title_contains": "avengers: doomsday",
        "venue": "ALUC",
        "dates": ["20261219", "20261220"],
        "format": "2d_only",
    },
]

# Seeded PCX Dune shows; each cycle also rediscovers any new PCX Screen 6 English Dune shows.
KNOWN_DUNE_PCX = [
    {"venue": "PRHN", "event_code": "ET00491771", "session_id": "259425",
     "date": "20261219", "time": "11:05 AM", "screen": "SCREEN 6 (PCX Screen)",
     "movie": "Dune: Part Three", "kind": "dune_pcx"},
    {"venue": "PRHN", "event_code": "ET00491771", "session_id": "259431",
     "date": "20261220", "time": "11:05 AM", "screen": "SCREEN 6 (PCX Screen)",
     "movie": "Dune: Part Three", "kind": "dune_pcx"},
]

DRILL_DATE = "20261219"
DRILL_VENUE = "ALUC"
DRILL_TITLE = "Avengers: Doomsday"
DRILL_MOVIE = "Avengers: Doomsday"

EXAMPLE_PAYLOAD = {
    "source": "github",
    "event": "dune_show_listed",
    "venue": "PRHN",
    "date": "20261219",
    "movie": "Dune: Part Three",
    "shows": [{
        "time": "11:05 AM",
        "screen": "SCREEN 6 (PCX Screen)",
        "format": "2D",
        "language": "English",
        "availability": "A",
        "min_price": "295.00",
        "max_price": "295.00",
        "session_id": "0000",
        "event_code": "ET00000000",
    }],
}


def all_pairs():
    return [(t["id"], d) for t in TARGETS for d in t["dates"]]


def now_ist():
    return datetime.now(IST)


def log(msg):
    print(f"{now_ist().isoformat(timespec='seconds')} {msg}", flush=True)


PROXY = os.environ.get("BMS_PROXY", "").strip()
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


def _post(session, route, url, data, headers):
    """POST is not relayed through jina."""
    kwargs = {"timeout": 30, "data": data, "headers": headers}
    if route == "warp":
        kwargs["proxies"] = {"http": PROXY, "https": PROXY}
    return session.post(url, **kwargs)


def fetch(session_box, venue, date_code):
    """Routes: WARP proxy (BMS blocks GitHub/Azure IPs directly), then direct, then r.jina.ai relay."""
    url = f"https://in.bookmyshow.com/api/v2/mobile/showtimes/byvenue?venueCode={venue}&dateCode={date_code}"
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
                log(f"{venue} {date_code} fetch failed {last}")
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


def title_matches(title, needle):
    return (needle or "").lower() in (title or "").lower()


def is_3d_format(child, show):
    dim = child.get("EventDimension") or ""
    attrs = show.get("Attributes") or ""
    return "3d" in f"{dim} {attrs}".lower()


def screen_label(show):
    name = (show.get("ScreenName") or "").strip()
    attrs = (show.get("Attributes") or "").strip()
    if attrs and attrs.lower() not in name.lower():
        return f"{name} ({attrs})" if name else attrs
    return name


def show_payload(child, show):
    return {
        "time": show.get("ShowTime"),
        "screen": screen_label(show),
        "format": child.get("EventDimension") or show.get("Attributes") or "",
        "language": child.get("EventLanguage") or "",
        "availability": show.get("Availability"),
        "min_price": show.get("MinPrice"),
        "max_price": show.get("MaxPrice"),
        "session_id": show.get("SessionId"),
        "event_code": child.get("EventCode"),
        "attributes": show.get("Attributes") or "",
    }


def build_payload(venue, date_code, shows, movie, **extra):
    body = {
        "source": "github",
        "event": "dune_show_listed",
        "venue": venue,
        "date": date_code,
        "movie": movie,
        "shows": shows,
    }
    body.update(extra)
    return body


def analyze(data, date_code, title_contains, format_mode="any"):
    """Open date + matching EventTitle + English child events, optional 2D-only filter."""
    dates = data.get("ShowDatesArray") or []
    open_flag = any(str(d.get("DateCode")) == date_code and d.get("isDisabled") is False for d in dates)
    details = data.get("ShowDetails") or []
    trusted = bool(details) and str(details[0].get("Date")) == date_code
    empty = {"open": False, "english": 0, "matches": []}
    if not open_flag or not trusted:
        return empty
    english, matches = 0, []
    for event in details[0].get("Event") or []:
        title = event.get("EventTitle") or ""
        if not title_matches(title, title_contains):
            continue
        for child in event.get("ChildEvents") or []:
            if not is_english(child.get("EventLanguage") or ""):
                continue
            for show in child.get("ShowTimes") or []:
                if format_mode == "2d_only" and is_3d_format(child, show):
                    continue
                english += 1
                matches.append(show_payload(child, show))
    matches.sort(key=lambda m: (str(m.get("time") or ""), str(m.get("screen") or ""), str(m.get("format") or "")))
    return {"open": True, "english": english, "matches": matches}


def pair_key(movie_id, date_code):
    return f"{movie_id}:{date_code}"



def load_state():
    """Return (fired_pairs set, seat_alerts dict). Legacy fired_dates are ignored."""
    fired = set()
    extra = os.environ.get("FIRED_PAIRS", "").strip()
    if extra:
        fired.update(p.strip() for p in extra.split(",") if p.strip())
    alerts = {}
    try:
        with open(STATE_PATH) as f:
            data = json.load(f)
        fired.update(str(p) for p in (data.get("fired_pairs") or []))
        alerts = data.get("seat_alerts") or {}
        if not isinstance(alerts, dict):
            alerts = {}
    except FileNotFoundError:
        pass
    except Exception as exc:
        log(f"could not read {STATE_PATH}: {type(exc).__name__}")
    return fired, alerts


def save_state(fired, seat_alerts):
    payload = {
        "fired_pairs": sorted(fired),
        "seat_alerts": seat_alerts,
        "updated": now_ist().isoformat(timespec="seconds"),
    }
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f)
        f.write("\n")
    os.replace(tmp, STATE_PATH)
    log(f"saved state {STATE_PATH} fired_pairs={sorted(fired)} seat_shows={sorted(seat_alerts)}")


def load_fired():
    fired, _ = load_state()
    return fired


def save_fired(fired):
    _, alerts = load_state()
    save_state(fired, alerts)


def fetch_seat_layout(session_box, venue, session_id, event_code, date_code):
    """GETSEATLAYOUT via WARP then direct. Does not call INITTRANS or ADDSEATS."""
    url = seats.SEAT_LAYOUT_URL
    data = seats.trans_form(venue, session_id)
    headers = seats.trans_headers(venue, session_id, event_code, date_code)
    routes = (["warp"] if PROXY else []) + ["direct"]
    last, status = None, None
    for attempt in range(2):
        for route in routes:
            status = None
            try:
                resp = _post(session_box[0], route, url, data, headers)
                status = resp.status_code
                if status != 200:
                    raise ValueError(f"HTTP {status}")
                layout = seats.decode_layout_payload(resp.text or "")
                LAST_ROUTE[0] = route
                return status, layout, None
            except Exception as exc:
                last = f"route={route} status={status} {type(exc).__name__}: {str(exc)[:160]}"
                log(f"{venue} {session_id} seat fetch failed {last}")
                try:
                    session_box[0].close()
                except Exception:
                    pass
                session_box[0] = new_session()
        if attempt < 1:
            time.sleep(random.uniform(2, 5))
    return status, None, last


def session_key(sess):
    return f"{sess['venue']}:{sess['session_id']}"


def discover_seat_sessions(session_box):
    """Known PCX Dune sessions plus any new PRHN PCX English Dune, plus ALUC Avengers 2D."""
    found = {}
    for sess in KNOWN_DUNE_PCX:
        found[session_key(sess)] = dict(sess)
    for t in TARGETS:
        for d in t["dates"]:
            status, data = fetch(session_box, t["venue"], d)
            if data.get("_error"):
                log(f"discover {t['venue']} {d} error {data['_error']}")
                continue
            a = analyze(data, d, t["title_contains"], t["format"])
            for m in a.get("matches") or []:
                if not m.get("session_id"):
                    continue
                if t["id"] == "dune" and not seats.is_pcx_show(m):
                    continue
                kind = "dune_pcx" if t["id"] == "dune" else "avengers_2d"
                rec = {
                    "venue": t["venue"],
                    "event_code": m.get("event_code"),
                    "session_id": str(m.get("session_id")),
                    "date": d,
                    "time": m.get("time"),
                    "screen": m.get("screen"),
                    "movie": t["movie"],
                    "kind": kind,
                    "format": m.get("format"),
                }
                found[session_key(rec)] = rec
    return list(found.values())


def zone_for(sess, layout):
    if sess.get("kind") == "dune_pcx":
        return seats.dune_pcx_zone()
    return seats.equivalent_zone(layout)


def inspect_seats(session_box, sess):
    status, layout, err = fetch_seat_layout(
        session_box, sess["venue"], sess["session_id"], sess["event_code"], sess["date"]
    )
    if err or layout is None:
        return {"ok": False, "error": err, "status": status, "blocks": [], "zone": None, "layout": None}
    zone = zone_for(sess, layout)
    blocks = seats.find_blocks(layout, zone)
    return {"ok": True, "error": None, "status": status, "blocks": blocks, "zone": zone, "layout": layout}


def seats_opened_payload(sess, zone, blocks, fresh=None):
    return {
        "source": "github",
        "event": "seats_opened",
        "movie": sess["movie"],
        "venue": sess["venue"],
        "date": sess["date"],
        "session_id": sess["session_id"],
        "event_code": sess["event_code"],
        "time": sess.get("time"),
        "screen": sess.get("screen"),
        "zone": zone.get("label") if zone else None,
        "blocks": blocks,
        "new_blocks": fresh if fresh is not None else blocks,
    }


def remaining_work(fired, needed, sessions, now=None):
    if any(p not in fired for p in needed):
        return True
    now = now or now_ist()
    for sess in sessions:
        if not seats.past_showtime(sess.get("date"), sess.get("time"), now):
            return True
    return False


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


def run_drill(session_box, key):
    """One-shot end-to-end alert drill. Never reads or writes fired_pairs or seat_alerts."""
    log(f"DRILL start venue={DRILL_VENUE} date={DRILL_DATE} movie={DRILL_MOVIE!r} (no state touch)")
    status, data = fetch(session_box, DRILL_VENUE, DRILL_DATE)
    if data.get("_error"):
        log(f"DRILL fetch error status={status} {data['_error']}")
        print("WEBHOOK_HTTP_STATUS none")
        write_output(webhook_status="none", drill_posted="false")
        return 1
    a = analyze(data, DRILL_DATE, title_contains=DRILL_TITLE, format_mode="any")
    returned = None
    det = data.get("ShowDetails") or []
    if det:
        returned = det[0].get("Date")
    log(
        f"DRILL {DRILL_VENUE} {DRILL_DATE} status={status} route={LAST_ROUTE[0]} "
        f"returned_date={returned} open={a['open']} matches={len(a['matches'])}"
    )
    if not a["matches"]:
        log("DRILL no English Avengers: Doomsday shows found; not posting")
        print("WEBHOOK_HTTP_STATUS none")
        write_output(webhook_status="none", drill_posted="false")
        return 1
    body = build_payload(DRILL_VENUE, DRILL_DATE, a["matches"], DRILL_MOVIE, drill=True)
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


def run_test(session_box, extra_dates):
    summary = {}
    would = []
    queries = []
    for t in TARGETS:
        dates = list(t["dates"])
        if t is TARGETS[0]:
            dates += [d for d in extra_dates if d and d not in dates]
        for d in dates:
            queries.append((t, d))
    for t, d in queries:
        label = f"{t['venue']}:{d}:{t['id']}"
        status, data = fetch(session_box, t["venue"], d)
        err = data.get("_error")
        a = analyze(data, d, t["title_contains"], t["format"]) if not err else {"open": False, "english": 0, "matches": []}
        returned = None
        if not err:
            det = data.get("ShowDetails") or []
            returned = det[0].get("Date") if det else None
        summary[label] = {
            "status": status,
            "route": LAST_ROUTE[0] if not err else None,
            "error": err,
            "venue": t["venue"],
            "date": d,
            "movie": t["movie"],
            "returned_date": returned,
            "open": a["open"],
            "english_matches": a["english"],
            "matches": len(a["matches"]),
            "match_times": [f'{m["time"]} {m["screen"]} {m["format"]} {m["language"]}' for m in a["matches"]],
        }
        log(f"TEST {label}: {json.dumps(summary[label])}")
        if a["matches"]:
            body = build_payload(t["venue"], d, a["matches"], t["movie"])
            would.append(body)
            log("WOULD_BE_PAYLOAD " + json.dumps(body))
    print("TEST_SUMMARY " + json.dumps(summary))
    print("WOULD_BE_PAYLOADS " + json.dumps(would))
    if not would:
        log("no matching listing shows; no listing webhook would be posted")
        print("WOULD_BE_PAYLOAD_EXAMPLE " + json.dumps(EXAMPLE_PAYLOAD))

    sessions = discover_seat_sessions(session_box)
    seat_reports = []
    for sess in sessions:
        if seats.past_showtime(sess.get("date"), sess.get("time")):
            log(f"SEAT skip past showtime {session_key(sess)} {sess.get('time')}")
            continue
        info = inspect_seats(session_box, sess)
        zone = info.get("zone") or {}
        rec = {
            "session": session_key(sess),
            "date": sess.get("date"),
            "time": sess.get("time"),
            "movie": sess.get("movie"),
            "screen": sess.get("screen"),
            "kind": sess.get("kind"),
            "ok": info["ok"],
            "error": info["error"],
            "route": LAST_ROUTE[0] if info["ok"] else None,
            "zone": zone.get("label"),
            "blocks": info["blocks"],
        }
        if info["ok"] and info.get("layout"):
            rec["row_summary"] = seats.layout_summary(info["layout"], zone)
        seat_reports.append(rec)
        log("SEAT_TEST " + json.dumps(rec, default=str))
        if info["blocks"]:
            log("WOULD_BE_SEATS_PAYLOAD " + json.dumps(seats_opened_payload(sess, zone, info["blocks"])))
        else:
            log(f"SEAT_TEST no qualifying blocks for {session_key(sess)} zone={zone.get('label')}")
    print("SEAT_TEST_SUMMARY " + json.dumps(seat_reports, default=str))

    ctrl_t, ctrl_d = TARGETS[0], (extra_dates[0] if extra_dates else TARGETS[0]["dates"][0])
    url = f"https://in.bookmyshow.com/api/v2/mobile/showtimes/byvenue?venueCode={ctrl_t['venue']}&dateCode={ctrl_d}"
    for route in (["warp"] if PROXY else []) + ["direct", "jina"]:
        try:
            resp = _get(new_session(), route, url)
            a = analyze(_parse(resp.text), ctrl_d, ctrl_t["title_contains"], ctrl_t["format"]) if resp.status_code == 200 else {"open": None, "matches": []}
            log(f"ROUTE {route} {ctrl_t['venue']} {ctrl_d}: status={resp.status_code} open={a.get('open')} matches={len(a.get('matches') or [])}")
        except Exception as exc:
            log(f"ROUTE {route} {ctrl_t['venue']} {ctrl_d}: {type(exc).__name__}: {str(exc)[:160]}")
    listing_ok = all(v["status"] == 200 and not v["error"] for v in summary.values())
    seats_ok = all(r["ok"] for r in seat_reports) if seat_reports else False
    return 0 if listing_ok and seats_ok else 1


def run_live(session_box, key, duration, interval):
    fired, seat_alerts = load_state()
    needed = {pair_key(mid, d) for mid, d in all_pairs()}
    log(f"watching listings {sorted(needed)}; already fired={sorted(fired)}")
    log(
        f"Dune PCX zone: {seats.dune_pcx_zone()['label']}; "
        f"Avengers 2D zone: back {seats.AVENGERS_BACK_FRAC:.0%} of rows, "
        f"central {seats.AVENGERS_CENTER_FRAC:.0%} of seats"
    )

    end = time.monotonic() + duration
    errors = 0
    sessions = []
    while True:
        now = now_ist()
        if now >= STOP_AT:
            log("reached stop time")
            write_output(all_fired="true")
            return 0
        try:
            sessions = discover_seat_sessions(session_box)
        except Exception as exc:
            errors += 1
            log(f"discover sessions failed {type(exc).__name__}: {str(exc)[:160]}")

        for t in TARGETS:
            for d in t["dates"]:
                key_id = pair_key(t["id"], d)
                status, data = fetch(session_box, t["venue"], d)
                if data.get("_error"):
                    errors += 1
                    log(f"{t['venue']} {d} {t['id']} fetch error {data['_error']}")
                    continue
                a = analyze(data, d, t["title_contains"], t["format"])
                already = key_id in fired
                log(
                    f"{t['venue']} {d} {t['id']} status={status} route={LAST_ROUTE[0]} "
                    f"open={a['open']} matches={len(a['matches'])} already_fired={already}"
                )
                if already or not a["matches"]:
                    continue
                body = build_payload(t["venue"], d, a["matches"], t["movie"])
                log("MATCH " + json.dumps(body))
                ok, _status = post_webhook(body, key)
                if ok:
                    fired.add(key_id)
                    save_state(fired, seat_alerts)
                    write_output(posted="true", posted_pair=key_id)
                    log(f"listing fired {key_id}; still watching seats")
                else:
                    log("webhook POST failed after retries; will retry next loop")

        for sess in sessions:
            sk = session_key(sess)
            if seats.past_showtime(sess.get("date"), sess.get("time"), now):
                log(f"SEAT skip past showtime {sk}")
                continue
            info = inspect_seats(session_box, sess)
            if not info["ok"]:
                errors += 1
                log(f"SEAT {sk} error {info['error']}")
                continue
            zone = info["zone"] or {}
            blocks = info["blocks"]
            log(
                f"SEAT {sk} status={info['status']} route={LAST_ROUTE[0]} "
                f"zone={zone.get('label')} blocks={len(blocks)}"
            )
            fire, fresh = seats.should_alert(blocks, seat_alerts.get(sk))
            if not fire:
                continue
            body = seats_opened_payload(sess, zone, blocks, fresh)
            log("SEATS_OPENED " + json.dumps(body))
            ok, _status = post_webhook(body, key)
            if ok:
                prev = set((seat_alerts.get(sk) or {}).get("block_ids") or [])
                prev.update(seats.block_ids(fresh))
                seat_alerts[sk] = {
                    "block_ids": sorted(prev),
                    "last_alert_at": now_ist().isoformat(timespec="seconds"),
                    "zone": zone.get("label"),
                }
                save_state(fired, seat_alerts)
                write_output(posted="true", posted_seats=sk)
            else:
                log("seats webhook POST failed after retries; will retry later")

        if not remaining_work(fired, needed, sessions, now):
            log("no listing or seat work remaining")
            write_output(all_fired="true")
            return 0
        if time.monotonic() + max(interval, 60) > end:
            break
        sleep_for = min(90, max(60, interval + random.uniform(-15, 15)))
        time.sleep(sleep_for)
    write_output(all_fired="true" if not remaining_work(fired, needed, sessions) else "false")
    return 1 if errors >= 6 else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="no POST; fetch listings and seat maps")
    ap.add_argument("--drill", action="store_true", help="POST a real drill alert for Avengers: Doomsday on 20261219 at ALUC; does not touch state")
    ap.add_argument("--extra", default="", help="comma-separated extra dates for test mode (applied to the first target)")
    ap.add_argument("--duration", type=float, default=270, help="seconds to keep polling")
    ap.add_argument("--interval", type=float, default=75)
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
        extra = [d for d in args.extra.split(",") if d]
        return run_test(sb, extra)
    return run_live(sb, key, args.duration, args.interval)


if __name__ == "__main__":
    sys.exit(main())
