# Project Docs

Suggested layout:

- `manuals/` for vendor manuals and datasheets
- `setup/` for installation and troubleshooting notes
- `hardware/` for wiring, ports, and lab-specific notes
- `experiment-control/` for future experiment-control documentation
- `measurement_file_format.md` for the native HDF5 measurement schema

Deferred design notes:

- [`experiment-control/experiment_plan_migration_plan.md`](./experiment-control/experiment_plan_migration_plan.md)
- [`experiment-control/experiment_plan_execution_model.md`](./experiment-control/experiment_plan_execution_model.md)
- [`experiment-control/pause_row_implementation_checklist.md`](./experiment-control/pause_row_implementation_checklist.md)

Keep large driver installers out of the repo unless offline reproducibility is required.
