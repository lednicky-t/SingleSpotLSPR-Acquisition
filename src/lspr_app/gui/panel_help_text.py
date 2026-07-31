"""Text content for the in-app panel "?" help buttons and Help-menu dialogs.

Kept separate from the widgets that display it so panel-layout code doesn't
get lost in long string literals, and so all in-app help copy lives in one
place to review and keep in sync with the UI as it changes.
"""

from __future__ import annotations

SPECTRAL_SOURCE_TITLE = "Spectral source"
SPECTRAL_SOURCE_TOOLTIP = "Acquisition source, dark/reference capture, and integration time."
SPECTRAL_SOURCE_BODY = (
    "Choose the acquisition source with the Spectrometer / Simulation tabs at the "
    "top of this panel.\n\n"
    "- Spectrometer: acquires from the connected device using Integration time, "
    "Accumulation (frame averaging), and the dark-count / nonlinearity correction "
    "checkboxes.\n"
    "- Simulation: generates a synthetic spectrum for testing and demos from the "
    "settings under the Simulation tab. It never touches spectrometer hardware.\n\n"
    "Switching tabs while continuous acquisition is running restarts it on the new "
    "source.\n\n"
    "Dark / Reference buttons (above the spectrum plot): while continuous "
    "acquisition is running, they cache the current live frame as the dark or "
    "reference baseline; otherwise they trigger a fresh one-shot acquisition. "
    "Absorbance display needs both a dark and a reference baseline captured first.\n\n"
    "Auto: automatically raises or lowers Integration time to target a good peak "
    "intensity, based on the current live signal."
)

SPECTRA_PROCESSING_TITLE = "Spectra processing"
SPECTRA_PROCESSING_TOOLTIP = "How the raw spectrum is processed for display and analysis."
SPECTRA_PROCESSING_BODY = (
    "Settings here control how the raw spectrum is turned into the displayed and "
    "analyzed spectrum - they don't change what the spectrometer captures, only how "
    "it's processed afterward.\n\n"
    "- Range: the wavelength window (Min/Max) and resolution used for peak, "
    "centroid, and fit analysis.\n"
    "- Baseline: the baseline-subtraction method applied before analysis.\n"
    "- Smoothing: Temporal averages recent processed spectra together (higher = "
    "smoother sensorgram, more lag); Spectral smooths across neighboring "
    "wavelengths within one spectrum.\n"
    "- Fitting (below): crop range and fit method used for the peak fit drawn on "
    "the spectrum plot.\n\n"
    "Save / Load buttons in this panel's header save the current processing "
    "settings to a file, or load a previously saved one - useful for repeating the "
    "same analysis setup across experiments."
)

SESSION_TITLE = "Session"
SESSION_TOOLTIP = "Live session settings/statistics summary, refresh rate, and stats logging."
SESSION_BODY = (
    "A live-updating, read-only summary of the current session's settings and "
    "performance statistics. Nothing in this panel affects acquisition.\n\n"
    "- Refresh rate: how often the GUI redraws live spectra and the sensorgram - "
    "independent of the underlying acquisition rate. Lower values skip more "
    "display frames and reduce GUI load without dropping any recorded data.\n"
    "- Camera icon: copy the current session panel snapshot to the clipboard.\n"
    "- Save icon: save the current session statistics to the experiment folder.\n"
    "- Record icon: start or stop a running log of session statistics over time."
)

LOG_TITLE = "Log"
LOG_TOOLTIP = "Live event log: filters, follow, copy/clear, and font size."
LOG_BODY = (
    "Live event log for acquisition, processing, and controller activity.\n\n"
    "- All / GUI / Devs buttons filter which messages are shown: All shows "
    "everything; GUI shows GUI, processing, and analysis messages only; Devs shows "
    "spectrometer, pump, valve, and switch messages only.\n"
    "- The follow icon keeps the view scrolled to the newest entry; turn it off to "
    "scroll back without being pulled back down.\n"
    "- The copy/trash icons copy or clear the visible log text.\n"
    "- The +/- icons adjust the log's font size (Ctrl+scroll wheel over the log "
    "does the same thing)."
)

SIMULATION_TITLE = "Simulation display model"
SIMULATION_TOOLTIP = (
    "Synthetic spectrum controls used in Simulation mode. These settings shape the "
    "generated display data, including the primary and secondary peaks, and do not "
    "affect spectrometer hardware."
)
SIMULATION_BODY = (
    "Synthetic spectrum controls used in Simulation mode. These settings shape the "
    "generated display data - they never touch spectrometer hardware, even when a "
    "real spectrometer is connected.\n\n"
    "- Primary peak: Peak center (wavelength), Peak width, and Peak height of the "
    "main synthetic peak.\n"
    "- Secondary peak: a second peak defined relative to the first - center offset, "
    "and height/width as a percentage of the primary peak's.\n"
    "- Baseline: constant offset added under the whole spectrum.\n"
    "- Relative slope: a linear tilt across the wavelength span, scaled by "
    "baseline + peak height.\n"
    "- Noise: random noise level added on top of the generated signal.\n\n"
    "Resolution: wavelength spacing of the synthetic spectrum grid.\n"
    "Output rate: how often the simulation backend produces a new frame."
)

EXPERIMENT_CONTROL_TITLE = "Experiment control"
EXPERIMENT_CONTROL_TOOLTIP = "Step editor, plan table, timeline, and run controls."
EXPERIMENT_CONTROL_BODY = (
    "Build and run a pump/valve/switch plan as a sequence of timed steps.\n\n"
    "Step editor (top): edit Duration, per-channel Flow/Direction/Tube, Color, "
    "Valve, and Switch for the selected step, then use Add / Apply / Duplicate / "
    "Remove to build the plan. The 'CHs' toggle switches between one shared "
    "direction+tube for all channels and per-channel editing.\n\n"
    "Plan table: lists every step; click a cell to edit it directly.\n\n"
    "Timeline (bottom strip): a to-scale view of the whole plan.\n"
    "- Single-click a step to select it (loads it into the step editor above).\n"
    "- Double-click a step to apply it to the pump immediately.\n"
    "- Scroll/drag to pan and zoom; the [Label: ...] button switches what's shown "
    "on each step (comment text or color name).\n\n"
    "Run controls: the play/hold button toggles the plan between running and "
    "holding at the current step; Stop halts it entirely; the arrow buttons step "
    "to the previous/next step manually; Pause freezes the plan clock without "
    "changing hardware state; the record button ties sensorgram recording to the "
    "plan run."
)

STATUS_READOUTS_TITLE = "Quick help"
STATUS_READOUTS_BODY = (
    "Status bar (bottom of the window):\n"
    "- Left message: current action status (e.g. \"Acquiring sample spectrum...\").\n"
    "- src / disp / proc / head / skip: src = source acquisition rate; disp = GUI "
    "display refresh rate; proc = processing time per spectrum; head = "
    "display-period / processing-time (above 1 means comfortable headroom); "
    "skip = dropped GUI updates per second.\n\n"
    "Spectral source panel:\n"
    "- spacing / rate / ovh: last frame spacing, effective source acquisition "
    "rate, and acquisition overhead (device latency beyond the requested "
    "integration/averaging budget). Applies to both the Spectrometer and "
    "Simulation tabs.\n\n"
    "Enable the Diagnostics panel (View menu) for a fuller per-frame telemetry "
    "breakdown - queue depths, deferred-refresh timings, and more - with a tooltip "
    "on every field there."
)
