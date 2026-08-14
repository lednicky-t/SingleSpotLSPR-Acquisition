"""Single source of truth for this app's Dark/Light theme colors.

Before this module existed, the same handful of colors (a muted gray for
"inactive" icons, a gold/yellow for "active mode" text, log-level colors,
etc.) were re-typed as raw hex literals at each call site - icon tints in
main_window.py, QSS in main_window_style.py and experiment_control_window.py,
the log terminal, the secondary-axis mode indicator, the HDF5 compression
icon, help buttons, the Preferences dialog. Most of those literals were only
ever tuned for the dark background: switching to Light theme didn't error,
it just silently produced a different, untested (and often near-invisible)
color, because there was nothing to make the "other" theme a first-class
citizen.

`theme_palette(theme_mode)` returns one dict of named colors for the given
mode. Add a new color here and reference it by name, rather than writing a
new hex literal at the call site - that way every consumer of a given
concept (e.g. "gold accent") moves together when the color is retuned, and a
new Light-theme value is never missing by omission.

A few tokens (see MainWindow._theme_palette / ExperimentControlWindow.
_theme_palette) are deliberately kept local to one window instead of living
here, because their Dark and Light values already diverged before this
module existed and unifying them would be a visible behavior change beyond
a refactor - not because they don't belong to the same idea.
"""

from __future__ import annotations

DARK_PALETTE: dict[str, str] = {
    # Core surfaces & text (shared by MainWindow and ExperimentControlWindow)
    "bg": "#13161b",
    "fg": "#e6ebf1",
    "field": "#171b21",
    "button": "#20252d",
    "button_hover": "#272d36",
    "button_pressed": "#303640",
    "accent_button": "#5d6876",
    "accent_hover": "#707d8c",
    "danger_button": "#8f5a61",
    "danger_hover": "#a46a72",
    "border": "#2b3138",
    "border_hover": "#414852",
    "pressed": "#252b33",
    "scroll": "#49505a",
    "scroll_hover": "#5c6470",
    "splitter": "#2b3138",

    # Gold/yellow "active mode" accent family - [Auto]/[XYY] toggle text,
    # the "Tool panel" header, the star marking the primary trace metric,
    # the edit-mode-active icon, the HDF5-compression-active icon.
    "mode_toggle_accent": "#e8d85f",
    "mode_toggle_muted": "#7a8291",
    "tool_panel_accent": "#e0a84a",
    "sensorgram_accent": "#8FE3A1",

    # Icon tints (pixmap-baked via tint_tabler_icon, not styleable through
    # QSS - every persistent icon button that needs to react live to a
    # theme switch goes through one of these two, not a fresh literal).
    "muted_icon": "#8a93a0",
    "accent_icon": "#8fbaff",

    # Log terminal (main_window_logging.py)
    "log_debug": "#5fa8ff",
    "log_info": "#c7d2e0",
    "log_success": "#44d07b",
    "log_warning": "#f4b23d",
    "log_error": "#ff6b6b",
    "log_critical": "#f35f8d",
    "log_timestamp": "#738193",
    "log_source": "#94a3b8",
    "log_message": "#e5edf7",

    # Secondary-axis mode indicator button ([XY]/[XYY]/[XY2Y])
    "secondary_axis_xy": "#7a8291",
    "secondary_axis_xyy": "#e8d85f",
    "secondary_axis_xy2y": "#f2994a",

    # HDF5 measurement-compression toggle icon
    "compression_active": "#f2c94c",
    "compression_inactive": "#b9a24b",

    # "?" help bubble (panel_help.py)
    "help_button_text": "#e6ebf1",
    "help_button_border": "rgba(230, 235, 241, 0.22)",
    "help_button_hover": "rgba(255, 255, 255, 0.06)",
    "help_button_pressed": "rgba(255, 255, 255, 0.10)",

    # Preferences dialog section boxes (main_window_preferences.py)
    "section_box_border": "rgba(255, 255, 255, 0.10)",
    "section_box_title": "#9fb3c5",
}

LIGHT_PALETTE: dict[str, str] = {
    "bg": "#ffffff",
    "fg": "#1d2733",
    "field": "#ffffff",
    "button": "#eef3f7",
    "button_hover": "#e6edf3",
    "button_pressed": "#dde9f3",
    "accent_button": "#2f80c1",
    "accent_hover": "#3e8dcf",
    "danger_button": "#d65a63",
    "danger_hover": "#e06a73",
    "border": "#d9e0e7",
    "border_hover": "#9dbbd4",
    "pressed": "#dde9f3",
    "scroll": "#bcc9d5",
    "scroll_hover": "#9fb3c5",
    "splitter": "#dde5ec",

    # Darkened versions of the dark-theme hues above: a color tuned for
    # contrast against near-black reads as barely-there on white, so Light
    # theme needs its own value, not a lighter/darker multiplier of the
    # dark one - see docs/schemas or git history for the concrete before/
    # after screenshots that motivated this.
    "mode_toggle_accent": "#9c6b08",
    "mode_toggle_muted": "#57606e",
    "tool_panel_accent": "#9c6b08",
    "sensorgram_accent": "#1f8a4c",

    "muted_icon": "#6b7684",
    "accent_icon": "#2f80c1",

    "log_debug": "#1c6fd6",
    "log_info": "#1d2733",
    "log_success": "#1f8a4c",
    "log_warning": "#9c6b08",
    "log_error": "#c62828",
    "log_critical": "#b0195a",
    "log_timestamp": "#5f6b78",
    "log_source": "#47515c",
    "log_message": "#1d2733",

    "secondary_axis_xy": "#57606e",
    "secondary_axis_xyy": "#9c6b08",
    "secondary_axis_xy2y": "#b5651d",

    "compression_active": "#8a6d00",
    "compression_inactive": "#8a7638",

    "help_button_text": "#1d2733",
    "help_button_border": "rgba(29, 39, 51, 0.28)",
    "help_button_hover": "rgba(29, 39, 51, 0.06)",
    "help_button_pressed": "rgba(29, 39, 51, 0.10)",

    "section_box_border": "rgba(29, 39, 51, 0.18)",
    "section_box_title": "#3f4c59",
}


def theme_palette(theme_mode: str) -> dict[str, str]:
    """Return the shared color palette for ``theme_mode`` ("light" or "dark", defaulting to dark)."""
    return dict(LIGHT_PALETTE if theme_mode == "light" else DARK_PALETTE)
