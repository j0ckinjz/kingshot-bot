# Changelog

## Current revised version

- Replaced basic API fetching with `curl_cffi` Chrome impersonation to better handle Cloudflare-protected gift-code API requests.
- Preserved the original gift-code casing during redemption because KingShot gift codes are case-sensitive.
- Added API retry handling and support for multiple gift-code response shapes.
- Changed `seen_codes.json` from simple claimed-player lists to structured per-code/per-player records.
- Added automatic in-memory migration support for legacy `seen_codes.json` data.
- Added structured redemption results with `status`, `message`, `terminal`, `success`, and `updated_at` fields.
- Added explicit result statuses for redeemed, already claimed, same-type already used, expired, claim limit reached, not found, server busy, not logged in, invalid player, requirements not met, and unknown.
- Added terminal vs retryable handling so final failures stop retrying while temporary failures continue on later checks.
- Kept `claim_limit_reached` retryable because claim limits may later be increased.
- Added `requirements_not_met` as a terminal status for accounts that do not meet redemption requirements.
- Added server-busy retry handling inside the Selenium redemption attempt.
- Improved Selenium reliability by clearing stale modals, verifying the gift-code input value, and waiting for the Confirm button to become usable.
- Preserved the single-Chrome-session-per-code flow for faster multi-player redemption.
- Added atomic JSON writes for safer `players.json` and `seen_codes.json` updates.
- Added locks around player and seen-code read/write operations.
- Added a redemption lock so scheduled, manual, and catch-up jobs do not redeem at the same time.
- Updated manual code processing, player catch-up, reset, clear-code, and remove-player logic for the structured tracking model.
- Improved Telegram admin output for players, codes, player status, manual runs, catch-up runs, and redemption summaries.
- Added safer Telegram message sending with chunking and Markdown fallback.
- Added timed rotating logs with retention.
- Improved redemption logs for modal messages, classifications, terminal vs retryable outcomes, and Selenium timeout diagnostics.
- Added safer debug screenshot filenames.
- Updated Ubuntu setup to create `.env`, logs, screenshots, Selenium cache, and a systemd service without overwriting existing config.
- Updated dependency installation to prefer `requirements.txt` and include `curl_cffi`.
- Updated Chrome installation to use the modern APT keyring method.
