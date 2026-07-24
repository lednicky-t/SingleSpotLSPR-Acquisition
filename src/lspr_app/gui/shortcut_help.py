from __future__ import annotations


def build_shortcuts_help_text() -> str:
    """All real, currently-wired keyboard shortcuts.

    Keep this in sync with the shortcuts actually installed in
    ``MainWindow._install_shortcuts`` and the menu-action shortcuts set in
    ``gui/chrome.py`` - it's meant to be a verified list, not a place for
    general panel/UI tips (those live in each panel's own "?" help button).
    """
    return (
        "Keyboard shortcuts:\n\n"
        "F1 = Quick help (status readout glossary)\n"
        "Ctrl+/ = This shortcuts list\n"
        "Ctrl+Q = Exit application\n"
        "Ctrl+Space = Start or stop measurement\n"
        "Ctrl+S = Save processing settings\n"
        "Ctrl+O = Load processing settings\n"
        "Ctrl+E = Export current plot\n"
        "Ctrl+L = Clear sensorgram/metric history\n"
        "Ctrl+Left = Previous pump-plan step\n"
        "Ctrl+Right = Next pump-plan step\n\n"
        "For what a panel's controls do and how to use them (timeline clicks, "
        "the step editor, log filters, etc.), click the '?' button in that "
        "panel's header."
    )
