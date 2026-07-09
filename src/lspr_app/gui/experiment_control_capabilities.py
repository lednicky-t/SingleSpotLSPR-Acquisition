from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExperimentControlCapabilities:
    devices_enabled: bool = True
    runtime_control_enabled: bool = True
    plan_import_export_enabled: bool = True
    show_runtime_buttons: bool = True
    show_device_columns: bool = True
    show_device_status_strip: bool = True
    show_step_navigation_controls: bool = True

    @classmethod
    def acquisition(cls) -> "ExperimentControlCapabilities":
        return cls(
            devices_enabled=True,
            runtime_control_enabled=True,
            plan_import_export_enabled=True,
            show_runtime_buttons=True,
            show_device_columns=True,
            show_device_status_strip=True,
            show_step_navigation_controls=True,
        )

    @classmethod
    def evaluation(cls) -> "ExperimentControlCapabilities":
        return cls(
            devices_enabled=False,
            runtime_control_enabled=False,
            plan_import_export_enabled=True,
            show_runtime_buttons=False,
            show_device_columns=False,
            show_device_status_strip=False,
            show_step_navigation_controls=False,
        )
