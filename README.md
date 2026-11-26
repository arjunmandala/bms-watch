# bms-watch

GitHub Actions poller for BookMyShow targets on Sat 19 and Sun 20 Dec 2026 (3 seats, booking is handled after the webhook wake):

1. English *Dune: Part Three* at Prasads Multiplex Hyderabad (`PRHN`), any format. The payload includes the screen name (and PCX attributes) so PCX can be told apart from the other screens. Listing event: `dune_show_listed`.
2. English *Avengers: Doomsday* at Allu Cinemas Kokapet (`ALUC`), **2D only** (format and attributes must not contain `3D`). The 2D child event may use a different event code than the 3D listing.
3. English *Avengers: Doomsday* **2D only** on the Prasads **PCX Screen 6** (`PRHN`). Non-PCX Prasads screens and any 3D format (including Infinity Vision 3D on PCX) are ignored. Listing event: `show_listed`, once per date.

- Fires the webhook once per `(target id, date)` pair. The Allu Dune watch has been dropped.
- Does not disable itself after the first date or movie; it keeps watching listings and seats until every listing pair has fired and every watched show is past showtime, or until Sun 20 Dec 2026 23:59 IST.
- State of already-fired pairs lives on the `watch-state` branch (`fired-dates.json`, keyed as `movie_id:YYYYMMDD` in `fired_pairs`) and in the Actions cache.
- Scheduled hourly (`cron: '7 * * * *'`) only polls when repo variable `LIVE=1`. Each live run also self-chains the next one, because GitHub cron can skip hours.
- Polls BMS via Cloudflare WARP on GitHub runners, with the jina relay as fallback (BMS 403s GitHub IPs).
- Webhook URL is in `poll.py`. The key is the `WEBHOOK_KEY` repo secret. Never commit the key. Payloads include `movie`, `venue`, and the shows list.
- `workflow_dispatch` mode `drill` is an end-to-end alert test: it looks up English *Avengers: Doomsday* at ALUC on 19 Dec 2026 (any format) and, if found, POSTs the real webhook with `"drill": true`. Mode `drill-pcx` does the same for English 2D Avengers on Prasads PCX (`show_listed`). Drills use a separate concurrency group and do not read or write `fired_pairs`.

- Also watches seat maps for English Dune on Prasads PCX Screen 6 (sessions rediscovered each cycle; seeded 11:05 AM on 19/20 Dec), English 2D Avengers on that same PCX screen (same zone: rows H-N, seats 10-40), and, once listed, English 2D Avengers at Allu (back half of rows, central 60% of seat numbers). It POSTs `seats_opened` when 3 adjacent available seats appear in the target zone. Re-alerts only for new blocks, at most once per 15 minutes per show, and stops watching a show at showtime. Seat data comes from `GETSEATLAYOUT` on `services-in.bookmyshow.com/doTrans.aspx` (the same call the public seat-layout page uses). Seat and listing poll interval is 30-45 seconds.
