"""Local evidence workbench, separate from the legacy migration CLI.

Importing this package performs no ingestion, environment-file loading or model
calls. Use workbench.app.create_app for an isolated application instance.
"""