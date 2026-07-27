"""
utils/config_loader.py -- Loads and caches config/settings.yaml.

Design notes:
- Isolated from the host scanner's config.py entirely.
- Thread-safe, cached-on-first-load singleton (module-level lock) so
  1000+ signals/day don't each pay a disk + YAML parse cost.
- `reload_config()` is exposed for tests / hot-reload scenarios.
"""

from __future__ import annotations

import copy
import os
import threading
from typing import Any, Dict, Optional

import yaml

from .exceptions import ConfigError

_DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "settings.yaml")

_LOCK = threading.Lock()
_CACHE: Dict[str, Dict[str, Any]] = {}


def _read_yaml(path: str) -> Dict[str, Any]:
    if not os.path.isfile(path):
        raise ConfigError(f"Configuration file not found: {path}", context={"path": path})
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Malformed YAML in config file: {exc}", context={"path": path}) from exc

    if not isinstance(data, dict):
        raise ConfigError("Config root must be a mapping.", context={"path": path})
    return data


def load_config(path: Optional[str] = None, *, force_reload: bool = False) -> Dict[str, Any]:
    """Load (and cache) the module configuration.

    Returns a deep copy so callers can't accidentally mutate the shared cache.
    """
    resolved = os.path.abspath(path or _DEFAULT_CONFIG_PATH)
    with _LOCK:
        if force_reload or resolved not in _CACHE:
            _CACHE[resolved] = _read_yaml(resolved)
        return copy.deepcopy(_CACHE[resolved])


def reload_config(path: Optional[str] = None) -> Dict[str, Any]:
    return load_config(path, force_reload=True)


def get(config: Dict[str, Any], dotted_key: str, default: Any = None) -> Any:
    """Convenience dotted-path getter, e.g. get(cfg, 'risk.atr_sl_multiplier')."""
    node: Any = config
    for part in dotted_key.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def is_enabled(config: Dict[str, Any], engine_name: str) -> bool:
    return bool(get(config, f"engines.{engine_name}.enabled", True))
