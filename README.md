# bms-watch
GitHub Actions poller for BookMyShow ALUC Ramayana Hindi ~3 PM shows (14/15 Nov 2026).
Scheduled runs only poll when repo variable LIVE=1. Key is the WEBHOOK_KEY secret.
Polls BMS via Cloudflare WARP on GitHub runners (jina relay fallback).
