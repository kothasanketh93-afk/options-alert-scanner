// Cloudflare Pages Function: the Android app registers its FCM token here (POST /api/push-register).
// The site sits behind Cloudflare Access, which puts the signed-in e-mail in Cf-Access-Authenticated-User-Email.
// Devices live in the private R2 bucket (binding DATA) as push/devices.json; the bot reads that file to send pushes.
const KEY = "push/devices.json";
const MAX_PER_EMAIL = 5, MAX_TOTAL = 50;
const json = (o, status = 200) => new Response(JSON.stringify(o), { status, headers: { "content-type": "application/json", "cache-control": "no-store" } });

export async function onRequestPost({ request, env }) {
  const email = (request.headers.get("Cf-Access-Authenticated-User-Email") || "").toLowerCase();
  if (!email) return json({ error: "not signed in" }, 401);
  const origin = request.headers.get("Origin");
  if (origin && new URL(origin).host !== new URL(request.url).host) return json({ error: "bad origin" }, 403);
  if (!env.DATA) return json({ error: "R2 binding DATA missing" }, 503);

  let body;
  try { body = await request.json(); } catch { return json({ error: "bad json" }, 400); }
  const token = body && body.token;
  if (typeof token !== "string" || token.length < 20 || token.length > 4096) return json({ error: "bad token" }, 400);
  let strategies = body.strategies;
  if (strategies !== null && strategies !== undefined) {
    if (!Array.isArray(strategies) || strategies.length > 80 || !strategies.every((s) => typeof s === "string" && /^[a-z0-9_]{1,40}$/.test(s)))
      return json({ error: "bad strategies" }, 400);
  } else strategies = null;

  let doc = { devices: [] };
  const cur = await env.DATA.get(KEY);
  if (cur) { try { doc = await cur.json(); } catch { doc = { devices: [] }; } }
  let devices = (doc.devices || []).filter((d) => d.token !== token);
  devices.push({ token, email, strategies, updated: new Date().toISOString() });
  // keep only the newest MAX_PER_EMAIL devices per e-mail, and cap the file
  const mine = devices.filter((d) => d.email === email).sort((a, b) => (a.updated < b.updated ? 1 : -1)).slice(0, MAX_PER_EMAIL);
  devices = devices.filter((d) => d.email !== email).concat(mine).sort((a, b) => (a.updated < b.updated ? 1 : -1)).slice(0, MAX_TOTAL);
  await env.DATA.put(KEY, JSON.stringify({ devices }), { httpMetadata: { contentType: "application/json" } });
  return json({ ok: true, devices: mine.length });
}
