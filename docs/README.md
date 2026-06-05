# Project Docs

Suggested layout:

- `manuals/` for vendor manuals and datasheets
- `setup/` for installation and troubleshooting notes
- `hardware/` for wiring, ports, and lab-specific notes
- `experiment-control/` for future experiment-control documentation
- `measurement_file_format.md` for the native HDF5 measurement schema
- `processing_math.md` for the metric definitions and fit/analysis rules
- `runtime_pipeline_architecture.md` for the lossless acquisition, async file writing, and display-drop rules
- `CODEX_ARCHITECTURE_RAILS_V7.md` for the controlling acquisition/storage versus UI/analysis architecture rules
- `CODEX_IMPLEMENTATION_GUIDE_V8_LOSSLESS_ACQ_AND_LOSSY_UI.md` for the step-by-step lossless acquisition and lossy UI implementation guide
- `CODEX_RUNTIME_SIMPLICITY_GUIDE_V12.md` for the simple-runtime baseline and anti-orchestration rules

Deferred design notes:

- [`experiment-control/experiment_plan_migration_plan.md`](./experiment-control/experiment_plan_migration_plan.md)
- [`experiment-control/experiment_plan_execution_model.md`](./experiment-control/experiment_plan_execution_model.md)
- [`experiment-control/pause_row_implementation_checklist.md`](./experiment-control/pause_row_implementation_checklist.md)
- [`docs/architecture/apps/sLSPR/acq/two_level_gui_scheduler.md`](../../../../docs/architecture/apps/sLSPR/acq/two_level_gui_scheduler.md)

Keep large driver installers out of the repo unless offline reproducibility is required.
