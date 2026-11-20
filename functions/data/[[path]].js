// Cloudflare Pages Function: serves ONLY the few read-only files the dashboard needs, straight from the private R2 bucket
// (binding name: DATA). The whole site, including this function, sits behind Cloudflare Access, so only allowed e-mails reach it.
const ALLOWED = [
  /^(mcx\/)?state\/\d{4}-\d{2}-\d{2}\.json$/,
  /^(mcx\/)?ledger\/outcomes\.csv$/,
];

export async function onRequestGet({ params, env }) {
  const key = [].concat(params.path || []).join("/");
  if (!ALLOWED.some((re) => re.test(key))) return new Response("Not found", { status: 404 });
  const obj = await env.DATA.get(key);
  if (!obj) return new Response("Not found", { status: 404 });
  const type = key.endsWith(".csv") ? "text/csv; charset=utf-8" : "application/json; charset=utf-8";
  return new Response(obj.body, { headers: { "content-type": type, "cache-control": "no-store" } });
}
