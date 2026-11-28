"""Focused offline failure-path checks for the read-only deployment gate."""
from copy import deepcopy
from hashlib import sha256
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from databricks.sdk.service.jobs import Job, JobRunAs, JobSettings, NotebookTask, Source, Task
from deployment.check_fixture_deployment import (
    CATALOGS, FORMAT, HOST, PACKAGE, USER, fingerprint, require_table,
    source_job, target_plan,
)


def rejected(call):
    try:
        call()
    except ValueError:
        return
    raise AssertionError("Invalid fixture deployment was accepted")


def encoded_plan(plan):
    plan["plan_sha256"] = fingerprint({k: v for k, v in plan.items() if k != "plan_sha256"})
    return json.dumps(plan).encode(), plan["plan_sha256"]


objects = [
    {"catalog": catalog,
     "full_name": f"{catalog}.tgt_scope_{number % 14}.fixture_{number}",
     "target": {"schema": f"tgt_scope_{number % 14}", "table": f"fixture_{number}",
                "columns": [{"name": "ID", "type": "INT", "nullable": True}]}}
    for catalog in CATALOGS for number in range(307)
]
plan = {"format": FORMAT, "host": HOST, "user": USER, "package_sha256": PACKAGE,
        "catalogs": list(CATALOGS), "targets_per_catalog": 307,
        "schemas_per_catalog": 14, "objects": objects}
raw, checksum = encoded_plan(plan)
assert len(target_plan(raw, checksum)["objects"]) == 921

foreign = deepcopy(plan)
foreign["objects"][0]["catalog"] = "unapproved_catalog"
raw, checksum = encoded_plan(foreign)
rejected(lambda: target_plan(raw, checksum))

duplicate = deepcopy(plan)
duplicate["objects"][1] = deepcopy(duplicate["objects"][0])
raw, checksum = encoded_plan(duplicate)
rejected(lambda: target_plan(raw, checksum))

raw, checksum = encoded_plan(plan)
tampered = json.loads(raw)
tampered["objects"][0]["target"]["columns"][0]["type"] = "STRING"
rejected(lambda: target_plan(json.dumps(tampered).encode(), checksum))

rejected(lambda: require_table({"same_count_but_wrong_identity": object()}, "canonical_fixture"))

path = "/Workspace/Users/" + USER + "/orion-synthetic-source-data/" + "0" * 32 + "/generate_sources"
job = Job(creator_user_name=USER, settings=JobSettings(
    run_as=JobRunAs(user_name=USER),
    tasks=[Task(task_key="seed", notebook_task=NotebookTask(
        notebook_path=path, source=Source.WORKSPACE))]))
downloads = []


def download(name, **_kwargs):
    downloads.append(name)
    return io.BytesIO(b"reviewed\n")


client = SimpleNamespace(jobs=SimpleNamespace(get=lambda _id: job),
                         workspace=SimpleNamespace(download=download))
reviewed = {"job_settings_sha256": fingerprint(job.settings.as_dict()),
            "runtime_sha256": sha256(b"reviewed\r\n").hexdigest()}
rejected(lambda: source_job(client, 1, reviewed))
assert len(downloads) == 1

downloads.clear()
job.settings.run_as.user_name = "unapproved_run_as"
reviewed["job_settings_sha256"] = fingerprint(job.settings.as_dict())
rejected(lambda: source_job(client, 1, reviewed))
assert downloads == []

print("Fixture deployment checks passed: scope, duplicates, tampering, exact identities, raw runtime bytes and Run-as")
