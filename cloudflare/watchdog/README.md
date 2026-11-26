# Watchdog (free, independent of GitHub)

Runs every minute on Cloudflare (free plan: 100k requests/day, 5 cron triggers). If the NSE or MCX state file stops updating during
market hours (>12 min) it sends a Telegram message. Holidays: no state file is created, so it stays quiet.

Deploy (Cloudflare dashboard, no CLI): Workers & Pages > Create > Worker > paste worker.js > Settings > Bindings: R2 bucket `DATA` =
`scanner-data` > Triggers: Cron `* * * * *` > Variables and Secrets (type Secret): `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
Or: `npx wrangler deploy` from this folder, then `npx wrangler secret put TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`.
