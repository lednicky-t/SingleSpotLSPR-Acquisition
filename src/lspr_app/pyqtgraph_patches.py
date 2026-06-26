"""Patches for known pyqtgraph 0.14.0 bugs that are not yet fixed upstream.

Call apply() once after QApplication is created, before any plots are shown.
"""
from __future__ import annotations

import importlib
import logging
import re
import xml.dom.minidom as xml_dom

import numpy as np

_log = logging.getLogger("lspr_app.pyqtgraph_patches")


def _patch_svg_exporter() -> None:
    """Fix SVGExporter.correctCoordinates crash on standalone SVG path commands.

    Qt's QSvgGenerator emits path data like 'M0,0 L100,50 Z'.  The 'Z'
    (closepath) token has no comma, so the original code's
        x, y = c.split(',')
    raises ValueError.  The fix: pass any token that has no comma through
    unchanged — those are standalone commands with no coordinates to transform.

    Bug present in pyqtgraph 0.14.0 (latest as of 2026-06).
    """
    try:
        mod = importlib.import_module("pyqtgraph.exporters.SVGExporter")
    except ImportError:
        return

    fn_module = importlib.import_module("pyqtgraph.functions")
    transformCoordinates = fn_module.transformCoordinates

    def _fixed_correctCoordinates(node, defs, item, options):  # noqa: N802
        # Fix gradients in defs (unchanged from original).
        for d in defs:
            if d.tagName == "linearGradient":
                d.removeAttribute("gradientUnits")
                from PyQt6.QtWidgets import QGraphicsItem  # noqa: F401 (import guard)
                for coord in ("x1", "x2", "y1", "y2"):
                    if coord.startswith("x"):
                        denominator = item.boundingRect().width()
                    else:
                        denominator = item.boundingRect().height()
                    try:
                        percentage = round(float(d.getAttribute(coord)) * 100 / denominator)
                    except (ValueError, ZeroDivisionError):
                        continue
                    d.setAttribute(coord, f"{percentage}%")
                for child in filter(
                    lambda e: isinstance(e, xml_dom.Element) and e.tagName == "stop",
                    d.childNodes,
                ):
                    offset = child.getAttribute("offset")
                    try:
                        child.setAttribute("offset", f"{round(float(offset) * 100)}%")
                    except ValueError:
                        continue

        groups = node.getElementsByTagName("g")

        # Split groups with mixed text/non-text children (unchanged from original).
        groups2 = []
        for grp in groups:
            subGroups = [grp.cloneNode(deep=False)]
            textGroup = None
            for ch in grp.childNodes[:]:
                if isinstance(ch, xml_dom.Element):
                    if textGroup is None:
                        textGroup = ch.tagName == "text"
                    if ch.tagName == "text":
                        if textGroup is False:
                            subGroups.append(grp.cloneNode(deep=False))
                            textGroup = True
                    else:
                        if textGroup is True:
                            subGroups.append(grp.cloneNode(deep=False))
                            textGroup = False
                subGroups[-1].appendChild(ch)
            groups2.extend(subGroups)
            for sg in subGroups:
                node.insertBefore(sg, grp)
            node.removeChild(grp)
        groups = groups2

        for grp in groups:
            matrix = grp.getAttribute("transform")
            match = re.match(r"matrix\((.*)\)", matrix)
            if match is None:
                vals = [1, 0, 0, 1, 0, 0]
            else:
                vals = [float(a) for a in match.groups()[0].split(",")]
            tr = np.array([[vals[0], vals[2], vals[4]], [vals[1], vals[3], vals[5]]])

            removeTransform = False
            for ch in grp.childNodes:
                if not isinstance(ch, xml_dom.Element):
                    continue
                if ch.tagName == "polyline":
                    removeTransform = True
                    coords = np.array(
                        [
                            [float(a) for a in c.split(",")]
                            for c in ch.getAttribute("points").strip().split(" ")
                        ]
                    )
                    coords = transformCoordinates(tr, coords, transpose=True)
                    ch.setAttribute(
                        "points",
                        " ".join([",".join([str(a) for a in c]) for c in coords]),
                    )
                elif ch.tagName == "path":
                    removeTransform = True
                    newCoords = ""
                    oldCoords = ch.getAttribute("d").strip()
                    if oldCoords == "":
                        continue
                    for c in oldCoords.split(" "):
                        if not c:
                            continue
                        # FIX: standalone SVG path commands (Z, z, H, V, etc.)
                        # have no coordinate pair — pass them through unchanged.
                        if "," not in c:
                            newCoords += c + " "
                            continue
                        x, y = c.split(",")
                        if x[0].isalpha():
                            t = x[0]
                            x = x[1:]
                        else:
                            t = ""
                        nc = transformCoordinates(
                            tr, np.array([[float(x), float(y)]]), transpose=True
                        )
                        newCoords += t + str(nc[0, 0]) + "," + str(nc[0, 1]) + " "
                    # If coords start with L instead of M, path won't render.
                    if newCoords and newCoords[0] != "M":
                        newCoords = "M" + newCoords[1:]
                    ch.setAttribute("d", newCoords)
                elif ch.tagName == "text":
                    removeTransform = False
                    families = ch.getAttribute("font-family").split(",")
                    if len(families) == 1:
                        from PyQt6.QtGui import QFont
                        font = QFont(families[0].strip('" '))
                        if font.styleHint() == QFont.StyleHint.SansSerif:
                            families.append("sans-serif")
                        elif font.styleHint() == QFont.StyleHint.Serif:
                            families.append("serif")
                        elif font.styleHint() == QFont.StyleHint.Courier:
                            families.append("monospace")
                        ch.setAttribute(
                            "font-family",
                            ", ".join(
                                [f if " " not in f else f'"{f}"' for f in families]
                            ),
                        )

                if (
                    removeTransform
                    and ch.getAttribute("vector-effect") != "non-scaling-stroke"
                    and grp.getAttribute("stroke-width") != ""
                ):
                    w = float(grp.getAttribute("stroke-width"))
                    s = transformCoordinates(
                        tr, np.array([[w, 0], [0, 0]]), transpose=True
                    )
                    w = ((s[0] - s[1]) ** 2).sum() ** 0.5
                    ch.setAttribute("stroke-width", str(w))

                if (
                    options.get("scaling stroke") is True
                    and ch.getAttribute("vector-effect") == "non-scaling-stroke"
                ):
                    ch.removeAttribute("vector-effect")

            if removeTransform:
                grp.removeAttribute("transform")

    mod.correctCoordinates = _fixed_correctCoordinates
    _log.debug("Patched pyqtgraph SVGExporter.correctCoordinates (Z-command fix).")


def _patch_matplotlib_exporter() -> None:
    """Guard the Matplotlib exporter against missing matplotlib.

    When matplotlib is not installed, clicking 'Matplotlib Window' in the
    export dialog raises an ImportError inside a PyQt6 slot — PyQt6 prints the
    traceback to stderr and swallows it, so the user sees nothing happen.

    Replace the export() call with one that shows a QMessageBox explaining
    that matplotlib must be installed.
    """
    try:
        mod = importlib.import_module("pyqtgraph.exporters.Matplotlib")
    except ImportError:
        return

    try:
        import matplotlib  # noqa: F401
        # matplotlib is present — no patch needed.
        return
    except ImportError:
        pass

    original_export = mod.MatplotlibExporter.export

    def _guarded_export(self, fileName=None):  # noqa: N803
        try:
            import matplotlib  # noqa: F401
        except ImportError:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                None,
                "Matplotlib not installed",
                "The Matplotlib export requires the <b>matplotlib</b> package.\n\n"
                "Install it with:\n\n"
                "    pip install matplotlib\n\n"
                "Then restart the application.",
            )
            return
        return original_export(self, fileName=fileName)

    mod.MatplotlibExporter.export = _guarded_export
    _log.debug("Patched pyqtgraph MatplotlibExporter (missing-matplotlib guard).")


def apply() -> None:
    """Apply all pyqtgraph patches.  Call once after QApplication is created."""
    _patch_svg_exporter()
    _patch_matplotlib_exporter()
