// Independent watchdog (Cloudflare Worker, free plan). Runs every minute, OUTSIDE GitHub, and tells you on Telegram if the bot's
// state stopped updating during market hours. Reads the same R2 bucket the dashboard uses (binding DATA).
// Secrets (set in the Cloudflare dashboard, never in git): TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
const STALE_MIN = 12;           // alert when last_run_at is older than this inside market hours
const REPEAT_MIN = 30;          // do not repeat the same alert more often than this
const IST_OFFSET_MS = 5.5 * 3600e3;

function istNow() { return new Date(Date.now() + IST_OFFSET_MS); }
const pad = n => String(n).padStart(2, "0");

async function lastRun(env, key) {
  const obj = await env.DATA.get(key);
  if (!obj) return null;
  try { return Date.parse((await obj.json()).last_run_at); } catch (e) { return null; }
}

async function tg(env, text) {
  if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_CHAT_ID) return;
  await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ chat_id: env.TELEGRAM_CHAT_ID, text }),
  });
}

export default {
  async scheduled(event, env, ctx) {
    const ist = istNow(), wd = ist.getUTCDay(), mins = ist.getUTCHours() * 60 + ist.getUTCMinutes();
    if (wd === 0 || wd === 6) return;
    const date = `${ist.getUTCFullYear()}-${pad(ist.getUTCMonth() + 1)}-${pad(ist.getUTCDate())}`;
    const checks = [];
    if (mins >= 9 * 60 + 25 && mins <= 15 * 60 + 25) checks.push(["NSE", `state/${date}.json`]);
    if (mins >= 9 * 60 + 15 && mins <= 23 * 60 + 25) checks.push(["MCX", `mcx/state/${date}.json`]);
    for (const [name, key] of checks) {
      const t = await lastRun(env, key);
      const age = t ? Math.round((Date.now() - t) / 60000) : null;
      if (age !== null && age <= STALE_MIN) continue;
      if (age === null && mins < 9 * 60 + 40) continue;               // file for today not created yet (holiday / early)
      const flag = `alerted:${name}`;
      const prev = await env.DATA.get(flag);
      if (prev && Date.now() - Number(await prev.text()) < REPEAT_MIN * 60000) continue;
      await env.DATA.put(flag, String(Date.now()));
      await tg(env, `⚠ ${name} bot: no state update for ${age === null ? "today (no file)" : age + " min"} (watchdog, independent of GitHub). Check GitHub Actions.`);
    }
  },
  async fetch() { return new Response("watchdog ok"); },
};
