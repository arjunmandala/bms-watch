# bms-watch

GitHub Actions poller for two BookMyShow targets on Sat 19 and Sun 20 Dec 2026 (3 seats, booking is handled after the webhook wake):

1. English *Dune: Part Three* at Prasads Multiplex Hyderabad (`PRHN`), any format. The payload includes the screen name (and PCX attributes) so PCX can be told apart from the other screens.
2. English *Avengers: Doomsday* at Allu Cinemas Kokapet (`ALUC`), **2D only** (format and attributes must not contain `3D`). The 2D child event may use a different event code than the 3D listing.

- Fires the webhook once per `(movie, date)` pair. The Allu Dune watch has been dropped.
- Does not disable itself after the first date or movie; it keeps watching until all four pairs have fired or until Sun 20 Dec 2026 23:59 IST.
- State of already-fired pairs lives on the `watch-state` branch (`fired-dates.json`, keyed as `movie_id:YYYYMMDD` in `fired_pairs`) and in the Actions cache.
- Scheduled hourly (`cron: '7 * * * *'`) only polls when repo variable `LIVE=1`. Each live run also self-chains the next one, because GitHub cron can skip hours.
- Polls BMS via Cloudflare WARP on GitHub runners, with the jina relay as fallback (BMS 403s GitHub IPs).
- Webhook URL is in `poll.py`. The key is the `WEBHOOK_KEY` repo secret. Never commit the key. Payloads include `movie`, `venue`, and the shows list.
- `workflow_dispatch` mode `drill` is an end-to-end alert test: it looks up English *Avengers: Doomsday* at ALUC on 19 Dec 2026 (any format) and, if found, POSTs the real webhook with `"drill": true`. Drill uses a separate concurrency group and does not read or write `fired_pairs`.
