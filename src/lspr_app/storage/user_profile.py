"""Lightweight per-user identity for a shared-Windows-login lab PC.

No authentication - this is bookkeeping ("who is running this instrument
right now"), not access control. Several people share one Windows login on
the instrument PC, so per-user settings isolation and HDF5 traceability
both need the app's own identity concept rather than relying on the OS
account (which today gives no separation at all).

Deliberately its own small module, not a shared package function yet: only
singleLSPR Acquisition uses this so far (see the phased rollout decision -
the Evaluation apps' more tangled settings stores are a separate follow-up).
If a second app needs the same registry, promote this to lspr_core then,
with a real second caller to design against.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from platformdirs import user_config_dir

from lspr_app.storage.output_paths import safe_path_component

_logger = logging.getLogger(__name__)

_SHARED_CONFIG_DIR = Path(user_config_dir("lspr-suite", appauthor=False))
_REGISTRY_PATH = _SHARED_CONFIG_DIR / "lspr_users.json"

# The pre-user settings file - unchanged path/format. Also the explicit
# global scope for the one setting (enabled_devices) that describes the
# physical rig, not a person's preference - see device_lifecycle.py's
# load_enabled_devices/save_enabled_devices, which pass this path directly.
GLOBAL_CONFIG_PATH = _SHARED_CONFIG_DIR / "lspr_settings.json"


def _read_registry() -> dict:
    try:
        return json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        if _REGISTRY_PATH.exists():
            _logger.warning("User registry at %s was unreadable (%s); treating as empty.", _REGISTRY_PATH, exc)
        return {}


def _write_registry(payload: dict) -> None:
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _REGISTRY_PATH.with_suffix(_REGISTRY_PATH.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(_REGISTRY_PATH)


def registry_exists() -> bool:
    return _REGISTRY_PATH.exists()


def list_known_users() -> list[str]:
    names = _read_registry().get("known_users", [])
    return [str(name) for name in names] if isinstance(names, list) else []


def active_user() -> str | None:
    name = _read_registry().get("active_user")
    return str(name) if name else None


def user_settings_path(name: str) -> Path:
    safe_name = safe_path_component(name, fallback="user")
    return _SHARED_CONFIG_DIR / "users" / safe_name / "lspr_settings.json"


def current_config_path() -> Path:
    """The settings file the active user's preferences should read from
    and write to - falls back to the pre-user flat file if no user is
    active yet (shouldn't normally happen once a name has been entered in
    the recording-context row, but keeps every app_config.py function safe
    to call before that)."""
    name = active_user()
    return user_settings_path(name) if name else GLOBAL_CONFIG_PATH


def set_active_user(name: str) -> None:
    """Set the active user, adding them to the known list if new.

    The very first time this is ever called (no registry file exists yet),
    also copies the pre-existing flat settings file into this user's new
    per-user file, so nobody's current preferences silently vanish when
    this feature is first used. Later calls (switching to a different
    already-known user, or adding a second/third user) never touch the
    flat file again.
    """
    name = str(name or "").strip()
    if not name:
        return
    first_run = not registry_exists()
    registry = _read_registry()
    known = registry.get("known_users", [])
    known = [str(n) for n in known] if isinstance(known, list) else []
    if name not in known:
        known.append(name)
    registry["known_users"] = known
    registry["active_user"] = name
    _write_registry(registry)
    if first_run and GLOBAL_CONFIG_PATH.exists():
        _migrate_global_settings_to(name)


def remove_known_user(name: str) -> None:
    """Remove *name* from the look-up list (Preferences > Users). Only
    removes it from the registry - never deletes that user's actual
    settings file, so nothing is lost if they were removed by mistake or
    come back later. Refuses to remove the currently active user (switch
    to someone else first)."""
    name = str(name or "").strip()
    if not name or name == active_user():
        return
    registry = _read_registry()
    known = registry.get("known_users", [])
    known = [str(n) for n in known] if isinstance(known, list) else []
    if name not in known:
        return
    registry["known_users"] = [n for n in known if n != name]
    _write_registry(registry)


def _migrate_global_settings_to(name: str) -> None:
    try:
        payload = json.loads(GLOBAL_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("Could not migrate existing settings for new user %r: %s", name, exc)
        return
    dest = user_settings_path(name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _logger.info("Migrated existing settings into user profile: %s -> %s", GLOBAL_CONFIG_PATH, dest)
