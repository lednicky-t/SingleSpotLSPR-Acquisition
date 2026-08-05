# LSPR Experiment Control Plan Format

This document describes the native text format for editable LSPR experiment-control plans.

The preferred native extension is `.flow.yaml`. Regular `.yaml` and `.yml` are also accepted. The app keeps CSV/TXT import and export for compatibility with existing pump-plan tables, but YAML is the format intended to grow with future devices.

## Goals

- Human-readable and editable in a text editor.
- Explicit units instead of units hidden in column names.
- Stable device IDs with user-editable labels.
- Forward compatible with additional pumps, valves, switches, and other devices.
- Older app versions may skip unknown devices or fields while still loading supported parts.

## Compatibility Rules

- `format.name` should be `LSPR Experiment Plan`.
- `format.version` is required. Version `1` is the first native schema.
- Unknown top-level sections should be ignored with a warning.
- Unknown device IDs should be skipped with a warning.
- Missing devices mean "no action" for that device.
- Missing pump channels default to `flow: 0` and `direction: OFF`.
- Device IDs are stable identifiers, for example `pump_1` or `switch_1`.
- Labels are display text and may be changed without changing device identity.
- Newer versions may add fields, devices, or metadata without breaking older readers.

## Units

The current native writer exports:

```yaml
units:
  flow: uL/min
  time: s
  tube_diameter: mm
```

Native import accepts `uL/min` directly. It also accepts `ml/min` for flow and converts it to `uL/min`.

## Example

```yaml
format:
  name: LSPR Experiment Plan
  version: 1

metadata:
  created_by: LSPR Acquisition
  exported_at: '2026-05-02T14:30:00'
  notes: ''

units:
  flow: uL/min
  time: s
  tube_diameter: mm

devices:
  pumps:
    pump_1:
      label: Reglo ICC
      channels:
        ch1:
          label: CH1
          tube_mm: 0.25
        ch2:
          label: CH2
          tube_mm: 0.25
        ch3:
          label: CH3
          tube_mm: 0.25
        ch4:
          label: CH4
          tube_mm: 0.25
  valves:
    valve_1:
      labels:
        open: L
        close: R
      display_labels:
        open: Open
        close: Close
  switches:
    switch_1:
      ports:
        1: empty
        2: MCH
        3: SSC
        4: CB

steps:
  - id: 1
    duration_s: 300
    color: '#D0CECE'
    comment: H20_4F_3P
    devices:
      pump_1:
        ch1:
          flow: 0
          direction: CW
        ch2:
          flow: 0
          direction: CW
        ch3:
          flow: 10
          direction: CW
        ch4:
          flow: 20
          direction: CW
      valve_1:
        state: open
      switch_1:
        port: 3
```

## Extending Devices

A future two-pump plan can add another pump without changing the step format:

```yaml
devices:
  pumps:
    pump_1:
      label: Main pump
      channels:
        ch1: {label: CH1, tube_mm: 0.25}
    pump_2:
      label: Reagent pump
      channels:
        ch1: {label: Reagent A, tube_mm: 0.50}

steps:
  - id: 1
    duration_s: 60
    devices:
      pump_1:
        ch1: {flow: 20, direction: CW}
      pump_2:
        ch1: {flow: 50, direction: CCW}
```

Current app versions only apply `pump_1`, `valve_1`, and `switch_1`. Other device IDs are reported and skipped.

## CSV/TXT Compatibility

Two semicolon-separated table layouts are supported, both on import and (as
of the "External CSV" export option) on export. Which one a given import
file uses is auto-detected from its header row - there's nothing to
configure. On export, the app writes whichever layout you pick in the
"Export experiment plan" file dialog.

### Native/legacy layout ("Compatibility CSV/TXT")

This is this app's own historical export layout, still the default:

```text
Step;Ch-1 Flow [ml/min];Ch-1 Direction;Ch-1 Tubesize [mm];...;Time;Valve;Color;Descritption;;Solution;volume:?L
```

| Column | Meaning | Values |
|---|---|---|
| `Step` | 1-based step number | integer (regenerated on import) |
| `Ch-<n> Flow [ml/min]` | Channel *n* flow rate | mL/min, one column per channel (1-6) |
| `Ch-<n> Direction` | Channel *n* rotation | `CW` / `CCW` (or `OFF`) |
| `Ch-<n> Tubesize [mm]` | Channel *n* tube inner diameter | mm; only row 1's value is read |
| `Time` | Step duration | seconds, plain number |
| `Valve` | Valve 1 state | `L`/`R` (physical wiring, see "which direction is L?" prompt) or `Open`/`Close` |
| `Color` | Step color | any Qt-parsable color string, e.g. `#AEAAAA` |
| `Descritption` | Step description | free text (the misspelling matches the original file header and is intentional for compatibility) |
| `Solution` | Switch position | matches a configured switch-solution label, or a number `1`-`12` |

Only 4 channels are ever driven by this app (`ACTIVE_PUMP_CHANNELS` in
`domain/pump_plan.py`); columns for channels 5-6 are read/written for
compatibility with 6-channel source files but are otherwise unused.

### External layout ("FR/Direction" format)

Used by at least one other pump-control tool this lab works with. Example:

```text
FR1 [ml/min];Direction1;FR2 [ml/min];Direction2;FR3 [ml/min];Direction3;FR4 [ml/min];Direction4;Time;Valves;Notes
2.00E-02;aclckw;1.00E-02;aclckw;;;;;00:05:00;V1oV2cV3cV4c;Plastic buffer
```

| Column | Meaning | Values |
|---|---|---|
| `FR<n> [ml/min]` | Channel *n* flow rate | mL/min (accepts scientific notation, e.g. `2.00E-02`); blank means the channel is off |
| `Direction<n>` | Channel *n* rotation | `clckw` (clockwise → `CW`) / `aclckw` (anti-clockwise → `CCW`) |
| `Time` | Step duration | clock string `HH:MM:SS` (or `MM:SS`), e.g. `00:07:30` = 450 s |
| `Valves` | All 4 valve states, packed | `V1<o/c>V2<o/c>V3<o/c>V4<o/c>`, e.g. `V1oV2cV3cV4c` |
| `Notes` | Step description | free text |

Import/export notes:

- This app only drives valve **V1** (`o` = Open, `c` = Close). V2-V4 are
  parsed but ignored on import, and always written as closed (`c`) on
  export, since nothing else is wired to them.
- `aclcwk` (a common transposition of `aclckw`) is also accepted on
  import, but the app always **writes** `aclckw` - that's the spelling
  confirmed against a real file from this lab's external tool.
- There is no tube-diameter or color column in this format; export uses
  this app's current tube-size settings internally but doesn't write them,
  and imported steps get the default step color.
- A channel with flow `0` is exported with both its `FR<n>` and
  `Direction<n>` cells left blank, matching how source files from the
  external tool represent an unused channel.

For new workflows that don't need to interoperate with either external
tool, prefer `.flow.yaml`.
