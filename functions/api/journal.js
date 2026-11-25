// Cloudflare Pages Function: the "My trades" journal (GET /api/journal, PUT /api/journal).
// The site sits behind Cloudflare Access, which puts the signed-in e-mail in Cf-Access-Authenticated-User-Email; each e-mail has its own
// private journal file in the R2 bucket (binding DATA): journal/<sha256(email)>.json. Nothing here is public or shared between users.
const MAX_TRADES = 500;
const json = (o, status = 200) => new Response(JSON.stringify(o), { status, headers: { "content-type": "application/json", "cache-control": "no-store" } });
const num = (v, lo, hi) => typeof v === "number" && isFinite(v) && v >= lo && v <= hi;

async function keyFor(email) {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(email));
  return "journal/" + [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("") + ".json";
}

function clean(t) {
  if (!t || typeof t !== "object") return null;
  const o = {
    id: String(t.id || "").slice(0, 40),
    date: String(t.date || ""),
    symbol: String(t.symbol || "").toUpperCase(),
    strike: t.strike,
    dir: t.dir,
    qty: t.qty,
    entry: t.entry,
    exit: t.exit,
    reason: String(t.reason || "").slice(0, 20),
    note: String(t.note || "").slice(0, 200),
  };
  if (!/^[a-z0-9_-]{4,40}$/i.test(o.id) || !/^\d{4}-\d{2}-\d{2}$/.test(o.date) || !/^[A-Z0-9&-]{1,24}$/.test(o.symbol)) return null;
  if (!num(o.strike, 0.01, 1e6) || (o.dir !== "CE" && o.dir !== "PE") || !num(o.qty, 1, 1e7)) return null;
  if (!num(o.entry, 0.01, 1e6) || !num(o.exit, 0, 1e6)) return null;
  if (!["scanner", "other_alert", "own"].includes(o.reason)) o.reason = "own";
  return o;
}

async function who(request, env) {
  const email = (request.headers.get("Cf-Access-Authenticated-User-Email") || "").toLowerCase();
  if (!email) return [null, json({ error: "not signed in" }, 401)];
  if (!env.DATA) return [null, json({ error: "R2 binding DATA missing" }, 503)];
  return [await keyFor(email), null];
}

export async function onRequestGet({ request, env }) {
  const [key, err] = await who(request, env);
  if (err) return err;
  const obj = await env.DATA.get(key);
  if (!obj) return json({ trades: [] });
  try { return json({ trades: (await obj.json()).trades || [] }); } catch { return json({ trades: [] }); }
}

export async function onRequestPut({ request, env }) {
  const [key, err] = await who(request, env);
  if (err) return err;
  const origin = request.headers.get("Origin");
  if (origin && new URL(origin).host !== new URL(request.url).host) return json({ error: "bad origin" }, 403);
  let body;
  try { body = await request.json(); } catch { return json({ error: "bad json" }, 400); }
  if (!body || !Array.isArray(body.trades) || body.trades.length > MAX_TRADES) return json({ error: "bad trades" }, 400);
  const trades = body.trades.map(clean);
  if (trades.some((t) => !t)) return json({ error: "invalid trade in list" }, 400);
  await env.DATA.put(key, JSON.stringify({ trades, updated: new Date().toISOString() }), { httpMetadata: { contentType: "application/json" } });
  return json({ ok: true, count: trades.length });
}
