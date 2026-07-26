"""
configuration_service.py
-------------------------
Read-only snapshot of config.py's module-level constants. Never
writes to config.py, never calls reload/import machinery that could
affect the running scanner -- "view configuration" only. "Reload /
validate / export / import configuration" from the Part 8 spec are
NOT implemented here (see README.md): config.py is committed source,
not a runtime-mutable file, so a real "hot reload without restart"
would need a genuinely different config storage design, which is out
of scope for this phase rather than something to fake.

Defense in depth even though config.py currently holds no secrets
(verified by inspection): any constant name containing a
secret-shaped keyword is redacted rather than trusting that to stay
true forever.
"""

import config as config_mod

_REDACT_KEYWORDS = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "PRIVATE")


def _is_public_constant(name: str, value) -> bool:
    if not name.isupper():
        return False
    if name.startswith("_"):
        return False
    # Keep it JSON-serializable; skip functions/classes/modules if any sneak in.
    return isinstance(value, (str, int, float, bool, list, dict, tuple, type(None)))


def get_configuration() -> dict:
    snapshot = {}
    for name in dir(config_mod):
        value = getattr(config_mod, name)
        if not _is_public_constant(name, value):
            continue
        if any(keyword in name for keyword in _REDACT_KEYWORDS):
            snapshot[name] = "***REDACTED***"
        else:
            snapshot[name] = value
    return snapshot
