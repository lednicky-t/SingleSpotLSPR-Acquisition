"""Re-export shim over `lspr_acq_shell.user_profile` (Phase 1 shell
extraction, 2026-08-07) - the per-user identity registry now lives there
since LSPRimaging Acquisition needs the same "who's logged in" concept.
Kept here so every existing `from lspr_app.storage.user_profile import ...`
/`from lspr_app.storage import user_profile` call site in this app keeps
working unchanged. New code in this app should keep importing from here
(not `lspr_acq_shell` directly) so the app-internal import surface stays
stable regardless of where the implementation lives.

sLSPR acq never passes a `filename` to any of these - every call implicitly
uses `lspr_acq_shell.user_profile.DEFAULT_SETTINGS_FILENAME`
("lspr_settings.json", sLSPR acq's historical name), so on-disk per-user
settings files are unaffected by this move.

For tests exercising the registry's actual behavior (state, migration,
sanitization), test `lspr_acq_shell.user_profile` directly - that's where
the real module-level state (`_SHARED_CONFIG_DIR`, `_REGISTRY_PATH`, etc.)
now lives; patching attributes on this shim would not affect it.
"""
from __future__ import annotations

from lspr_acq_shell.user_profile import (
    GLOBAL_CONFIG_PATH,
    active_user,
    current_config_path,
    global_config_path,
    list_known_users,
    registry_exists,
    remove_known_user,
    safe_path_component,
    set_active_user,
    user_settings_path,
)

__all__ = [
    "GLOBAL_CONFIG_PATH",
    "active_user",
    "current_config_path",
    "global_config_path",
    "list_known_users",
    "registry_exists",
    "remove_known_user",
    "safe_path_component",
    "set_active_user",
    "user_settings_path",
]
