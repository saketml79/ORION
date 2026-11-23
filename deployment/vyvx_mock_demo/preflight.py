"""Read-only live metadata and installed SDK signature discovery. No secrets printed."""
import inspect
import json
import os
import importlib.metadata
from databricks.sdk import WorkspaceClient
from databricks.sdk.service import jobs

def client():
    for key in tuple(os.environ):
        if key.startswith("DATABRICKS_") and key != "DATABRICKS_CONFIG_FILE":
            del os.environ[key]
    return WorkspaceClient(profile="labs")

if __name__ == "__main__":
    w = client()
    user = w.current_user.me().user_name
    print("SDK", importlib.metadata.version("databricks-sdk"), "USER", user)
    for catalog in ("orion_bronze_test_ssuman", "orion_silver_test_ssuman", "orion_gold_test_ssuman"):
        info = w.catalogs.get(catalog)
        print("CATALOG", catalog, "OWNER", info.owner)
        print("DEMO_SCHEMAS", [(s.name, s.owner, s.comment, s.properties)
              for s in w.schemas.list(catalog_name=catalog) if "vyvx_mock_demo" == s.name])
    warehouse = w.warehouses.get("e3a0fc2c08db05bb")
    print("WAREHOUSE", warehouse.id, warehouse.enable_serverless_compute, warehouse.state)
    print("CREATE", inspect.signature(w.jobs.create))
    print("UPLOAD", inspect.signature(w.workspace.upload))
    print("IMPORT", inspect.signature(w.workspace.import_))
    print("GET_OUTPUT", inspect.signature(w.jobs.get_run_output))
    print("CONDITION", inspect.signature(jobs.ConditionTask))
    print("SETTINGS", inspect.signature(jobs.JobSettings))
    print("EXISTING_JOBS", [(j.job_id, j.creator_user_name, j.settings.name, j.settings.tags)
          for j in w.jobs.list(name="[MOCK ONLY] VYVX HIST PREP PULL")])