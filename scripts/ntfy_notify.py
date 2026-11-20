"""Phone push via ntfy.sh (added 2026-11-20). Telegram now carries only the end-of-day report; live trade alerts go here.

The topic name is the only secret (ntfy.sh topics are public unless ACLs are set), so it is NEVER in the repo: set the GitHub
Actions secret NTFY_TOPIC (a long random string) and subscribe to that same topic in the ntfy phone app.
Best-effort: no topic set, a network error or a rate limit never raises and never affects trading/tracking.
"""
import html
import json
import os
import re
import urllib.request

from logging_setup import get_logger

log = get_logger("ntfy")
SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
_TAG = re.compile(r"<[^>]+>")


def plain(text: str) -> str:
    return html.unescape(_TAG.sub("", text or "")).strip()


def _priority(title: str) -> int:
    t = title.lower()
    if "stop-loss" in t or "sl hit" in t or "failed" in t or "stopped" in t:
        return 4          # high: a trade was stopped out / something broke
    return 3              # default


def slug(strategy: str) -> str:
    """Strategy name -> topic-safe suffix (ntfy topics allow letters, digits, - and _)."""
    return re.sub(r"[^a-z0-9]+", "_", str(strategy or "").lower()).strip("_")[:40]


def topic_for(base: str, strategy: str = None) -> str:
    s = slug(strategy)
    return f"{base}-{s}" if s else base


def publish(text: str, priority: int = None, tags=None, strategy: str = None) -> bool:
    """Send one notification. First line becomes the title, the rest the body. Returns True if ntfy accepted it.
    With a strategy the message goes to topic "<NTFY_TOPIC>-<strategy>", so the phone app can subscribe per strategy;
    without one (failure alerts) it goes to the base topic."""
    base = os.environ.get("NTFY_TOPIC", "").strip()
    if not base:
        return False
    topic = topic_for(base, strategy)
    body = plain(text)
    if not body:
        return False
    first, _, rest = body.partition("\n")
    title = first[:120] or "Options Scanner"
    payload = {"topic": topic, "title": title, "message": (rest.strip() or first)[:3500],
               "priority": priority or _priority(title)}
    if tags:
        payload["tags"] = list(tags)
    try:
        req = urllib.request.Request(SERVER, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=6) as r:
            ok = 200 <= r.status < 300
        log.info("ntfy push %s (title=%r).", "sent" if ok else "rejected", title[:50])
        return ok
    except Exception as exc:                       # never let a push problem touch the run
        log.warning("ntfy push failed (%s: %s) -- continuing.", type(exc).__name__, exc)
        return False
