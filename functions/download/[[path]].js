// Cloudflare Pages Function: /download shows an install page, /download/options-scanner.apk serves the Android app from private R2.
// The whole site sits behind Cloudflare Access, so only allowed e-mails can open or download this.
const KEY = "app/options-scanner.apk";
const PAGE = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Options Scanner app</title>
<style>:root{color-scheme:dark}body{margin:0;background:#0f141f;color:#e4e9f2;font:16px/1.5 system-ui,sans-serif;display:flex;justify-content:center}main{max-width:460px;padding:28px 18px}
h1{font-size:22px;margin:0 0 4px}p{color:#8a95aa;margin:6px 0 16px}a.b{display:block;text-align:center;background:#1d2a44;border:1px solid #4a6a9f;color:#e4e9f2;text-decoration:none;font-weight:650;padding:14px;border-radius:999px;margin:18px 0}
ol{padding-left:20px;color:#c5cddd}li{margin:8px 0}small{color:#8a95aa}</style></head><body><main>
<h1>Options Scanner for Android</h1><p>Paper-trading alerts and dashboard. For education, not advice.</p>
<a class="b" href="/download/options-scanner.apk" download>Download the app (APK)</a>
<ol><li>Open the downloaded file. If Android asks, allow "Install unknown apps" for your browser or Files app.</li><li>Open the app, log in with your e-mail (a code is sent to you).</li><li>Allow notifications when asked, to get alerts even when the app is closed.</li></ol>
<small>Updates install over the old version, your data stays.</small></main></body></html>`;

export async function onRequestGet({ params, env }) {
  const sub = [].concat(params.path || []).join("/");
  if (sub === "" ) return new Response(PAGE, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });
  if (sub !== "options-scanner.apk") return new Response("Not found", { status: 404 });
  if (!env.DATA) return new Response("R2 binding DATA is missing", { status: 503 });
  let obj;
  try { obj = await env.DATA.get(KEY); } catch (e) { return new Response("R2 read failed: " + e.message, { status: 502 }); }
  if (!obj) return new Response("App not published yet", { status: 404 });
  return new Response(obj.body, { headers: {
    "content-type": "application/vnd.android.package-archive",
    "content-disposition": 'attachment; filename="options-scanner.apk"',
    "cache-control": "no-store",
  } });
}
