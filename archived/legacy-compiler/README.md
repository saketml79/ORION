# Archived legacy compiler

These folders contain the earlier standalone Informatica compiler and visualizer. The current ORION workbench does not import them. Their source is retained unchanged for reference and for the standalone pipeline.

To use the old pipeline from the repository root in PowerShell, add this archive to the Python module search path before running the module:

```powershell
$env:PYTHONPATH = (Resolve-Path .\archived\legacy-compiler).Path
python -m orchestration.run
```

The legacy pipeline reads its configured local inputs and writes to `output/`. Review its behavior and configuration before running it. It is separate from the workbench and does not run as part of the hosted app.
