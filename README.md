# bms-watch

GitHub Actions poller for BookMyShow English *Dune: Part Three* shows at Allu Cinemas Kokapet (ALUC) on Sat 19 and Sun 20 Dec 2026.

- Fires the webhook once per date, the moment any English Dune show is listed (any time, any format: Dolby Cinema, IMAX, 2D, 3D).
- Does not wait for a narrow time window. Does not disable itself after the first date; it keeps watching the other date until both have fired or until Sun 20 Dec 2026 23:59 IST.
- State of already-fired dates lives on the `watch-state` branch (`fired-dates.json`) and in the Actions cache, so a later run will not re-alert the same date.
- Scheduled hourly (`cron: '7 * * * *'`) only polls when repo variable `LIVE=1`. Each live run also self-chains the next one, because GitHub cron can skip hours.
- Polls BMS via Cloudflare WARP on GitHub runners, with the jina relay as fallback (BMS 403s GitHub IPs).
- Webhook URL is in `poll.py`. The key is the `WEBHOOK_KEY` repo secret. Never commit the key.
