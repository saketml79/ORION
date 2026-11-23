"""Durable review milestones, not an autonomous worker or execution authority.

Hosted SQLite is a disposable cache; delta_state explicitly maps every table.
Commit-last publication and the hosted single-writer restriction still apply.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
import re
import sqlite3
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from workbench import delta_state as ds
from workbench.target_review import target_review


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CohortRequest(Strict):
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    workflow_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    scope: Literal["workflow", "cohort"]
    input_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    scenario: Literal["original", "bronze"]
    consent: bool
    member_ids: list[str] | None = Field(default=None, max_length=200)
    scope_rationale: str = Field(default='', max_length=4000)
    owner: str = Field(default='', max_length=255)
    migration_unit: str = Field(default='', max_length=255)
    semantic_links: list[str] = Field(default_factory=list, max_length=100)


def reviewed_members(index, inventory, request):
    """Explicit overrides never erase the original literal-call boundary."""
    selected = request.get('member_ids')
    if selected is None:
        return sorted(inventory['workflow_ids'])
    if (not selected or len(selected) != len(set(selected))
            or request['workflow_id'] not in selected
            or any(not re.fullmatch(r'[a-f0-9]{64}', wid)
                   or index.nodes.get(wid, {}).get('kind') != 'workflow' for wid in selected)):
        raise HTTPException(422, 'Choose distinct canonical workflows including the selected workflow')
    if request['scope'] == 'workflow' and selected != [request['workflow_id']]:
        raise HTTPException(422, 'Individual scope cannot contain other workflows')
    if (set(selected) != set(inventory['workflow_ids'])
            and not all(request.get(key, '').strip() for key in ('scope_rationale', 'owner', 'migration_unit'))):
        raise HTTPException(422, 'A scope override requires rationale, responsible owner and migration unit')
    ds._safe(_json(request).encode())
    return sorted(selected)


class TargetRequest(Strict):
    workflow_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    scope: Literal["workflow", "cohort"]
    input_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_version: int = Field(ge=0)
    layer: Literal["bronze", "silver", "gold"]
    catalog: str = Field(min_length=1, max_length=255)
    schema_name: str = Field(min_length=1, max_length=255)
    table: str = Field(min_length=1, max_length=255)
    is_test_fixture: bool


class WorkflowAction(Strict):
    operation_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    consent: bool


class TargetVerification(Strict):
    workflow_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_version: int = Field(ge=1)
    consent: bool
    check_select: bool = False


def operation_id(package, cohort, workflow):
    return sha256(f"cohort-design-v1:{package}:{cohort}:{workflow}".encode()).hexdigest()[:32]


def update_workflow(service, package, cohort, workflow, **changes):
    with _db(service, write=True) as db:
        row = db.execute("SELECT payload FROM cohort_workflows WHERE package_hash=? AND run_id=? AND workflow_id=?",
                         (package, cohort, workflow)).fetchone()
        if row is None:
            raise HTTPException(404, "Unknown workflow in this saved review")
        value = json.loads(row['payload'])
        value.update(changes)
        db.execute("UPDATE cohort_workflows SET payload=? WHERE package_hash=? AND run_id=? AND workflow_id=?",
                   (_json(value), package, cohort, workflow))
        return value


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _identifier(value):
    # A single unquoted UC name, not a dotted SQL fragment or an expression.
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise HTTPException(422, "Catalog, schema and table must be simple UC identifiers")
    return value


def _inventory(index, workflow_id, scope, version):
    try:
        return target_review(index, workflow_id, scope, 0, 100, version)
    except ValueError:
        raise HTTPException(422, "Unknown workflow or target scope") from None


def _target(index, workflow_id, scope, version, target_id):
    offset = 0
    while True:
        result = target_review(index, workflow_id, scope, offset, 100, version)
        if any(row["target_id"] == target_id for row in result["targets"]["items"]):
            return result
        if not result["targets"]["has_more"]:
            raise HTTPException(404, "Target is not in this workflow scope")
        offset = result["targets"]["next_offset"]


@contextmanager
def _db(service, write=False):
    path = service.state / "cohort.sqlite"
    if not write and not path.exists():
        yield None
        return
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        if write:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS cohort_runs (
                    package_hash TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(package_hash,id));
                CREATE TABLE IF NOT EXISTS cohort_workflows (
                    package_hash TEXT NOT NULL, run_id TEXT NOT NULL, workflow_id TEXT NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(package_hash,run_id,workflow_id));
                CREATE TABLE IF NOT EXISTS target_selections (
                    package_hash TEXT NOT NULL, workflow_id TEXT NOT NULL, target_id TEXT NOT NULL,
                    version INTEGER NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(package_hash,workflow_id,target_id));
            """)
        db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def install_cohort_review(app, service, start_workflow=None):
    @app.post("/api/cohorts", status_code=201)
    def create_cohort(body: CohortRequest):
        if not body.consent:
            raise HTTPException(403, "Explicit consent is required to record the review scope")
        index = service.require()
        inventory = _inventory(index, body.workflow_id, body.scope, body.input_version)
        workflows = reviewed_members(index, inventory, body.model_dump())
        if len(workflows) > 200:
            raise HTTPException(422, "Review scope exceeds the 200-workflow bound")
        package = index.model["package"]["sha256"]
        request = body.model_dump()
        payload = {"id": body.request_id, "package_hash": package, "request": request,
                   "created_at": datetime.now(timezone.utc).isoformat(),
                   "status": "awaiting_individual_review", "autonomous_worker_available": False,
                   "execution_allowed": False, "deployment_allowed": False,
                   "basis": inventory["cohort_basis"],
                   "source_component_ids": inventory['workflow_ids'],
                   "excluded_member_ids": sorted(set(inventory['workflow_ids']) - set(workflows)),
                   "added_member_ids": sorted(set(workflows) - set(inventory['workflow_ids'])),
                   "architecture_approved": False,
                   "scope_notice": 'Owner-reviewed migration scope, not an approved execution DAG. Missing calls remain unverified.'}
        with _db(service, write=True) as db:
            old = db.execute("SELECT payload FROM cohort_runs WHERE package_hash=? AND id=?", (package, body.request_id)).fetchone()
            if old:
                saved = json.loads(old["payload"])
                if saved["request"] != request:
                    raise HTTPException(409, "Request ID already belongs to a different review scope")
            else:
                db.execute("INSERT INTO cohort_runs VALUES (?,?,?)", (package, body.request_id, _json(payload)))
                for wid in workflows:
                    row = {"workflow_id": wid, "name": index.nodes[wid]["name"],
                           "status": "awaiting_individual_review", "agent_run_id": None,
                              "operation_id": operation_id(package, body.request_id, wid),
                           "reason": "A durable independent model worker is not configured. Review and explicitly submit this workflow using the existing agent action."}
                    db.execute("INSERT INTO cohort_workflows VALUES (?,?,?,?)", (package, body.request_id, wid, _json(row)))
        return get_cohort(body.request_id)

    @app.get("/api/cohorts/{cohort_id}")
    def get_cohort(cohort_id: str):
        if not re.fullmatch(r"[a-f0-9]{32}", cohort_id):
            raise HTTPException(404, "Unknown cohort")
        index = service.require()
        package = index.model["package"]["sha256"]
        with _db(service) as db:
            row = db.execute("SELECT payload FROM cohort_runs WHERE package_hash=? AND id=?", (package, cohort_id)).fetchone() if db else None
            if row is None:
                raise HTTPException(404, "Unknown cohort in this package")
            result = json.loads(row["payload"])
            rows = db.execute(
                "SELECT workflow_id,payload FROM cohort_workflows WHERE package_hash=? AND run_id=? ORDER BY workflow_id", (package, cohort_id))
            result["workflows"] = []
            for entry in rows:
                workflow = json.loads(entry["payload"])
                if workflow.get("workflow_id") != entry["workflow_id"]:
                    raise HTTPException(409, "Saved workflow identity is inconsistent; inspect the committed review")
                result["workflows"].append(workflow)
            for workflow in result["workflows"]:
                workflow.setdefault("operation_id", operation_id(package, cohort_id, workflow["workflow_id"]))
        try:
            inventory = _inventory(index, result["request"]["workflow_id"], result["request"]["scope"], result["request"]["input_version"])
            if [row["workflow_id"] for row in result["workflows"]] != reviewed_members(index, inventory, result['request']):
                raise HTTPException(409, "Saved cohort membership is incomplete or inconsistent; inspect the committed review")
            result["stale"] = False
        except HTTPException as error:
            if error.status_code != 409:
                raise
            if error.detail != "Package evidence changed; refresh target review":
                raise
            result["stale"] = True
        return result

    @app.post("/api/cohorts/{cohort_id}/workflows/{workflow_id}/design", status_code=202)
    def start_design(cohort_id: str, workflow_id: str, body: WorkflowAction, request: Request):
        if not body.consent:
            raise HTTPException(403, "Explicit model data-egress consent is required")
        if not re.fullmatch(r"[a-f0-9]{32}", cohort_id) or not re.fullmatch(r"[a-f0-9]{64}", workflow_id):
            raise HTTPException(404, "Unknown review workflow")
        if service.package_cache_scope != 'local' and not request.scope.get('workbench.operation_execution'):
            raise HTTPException(409, "Hosted cohort design requires its recorded operation journal")
        index = service.require()
        package = index.model['package']['sha256']
        if body.operation_id != operation_id(package, cohort_id, workflow_id):
            raise HTTPException(409, "Operation does not match this workflow and package")
        saved = get_cohort(cohort_id)
        if saved['stale']:
            raise HTTPException(409, "Package evidence changed; create a current review scope")
        if start_workflow is None:
            raise HTTPException(503, "Design execution is unavailable")
        # A local claim is durable before inference. Hosted requests additionally
        # have a durable operation journal committed before entering this route.
        # An uncertain claim is never automatically retried.
        with _db(service, write=True) as db:
            row = db.execute("SELECT payload FROM cohort_workflows WHERE package_hash=? AND run_id=? AND workflow_id=?",
                             (package, cohort_id, workflow_id)).fetchone()
            if row is None:
                raise HTTPException(404, "Unknown workflow in this saved review")
            value = json.loads(row['payload'])
            if value['status'] != 'awaiting_individual_review':
                raise HTTPException(409, "Workflow was already claimed; inspect the saved operation and run before any retry")
            value.update(status='starting', operation_id=body.operation_id,
                         reason='Explicit design action claimed; inspect the recorded operation before retrying.')
            db.execute("UPDATE cohort_workflows SET payload=? WHERE package_hash=? AND run_id=? AND workflow_id=?",
                       (_json(value), package, cohort_id, workflow_id))
        try:
            return start_workflow(saved, workflow_id, cohort_id)
        except Exception:
            update_workflow(service, package, cohort_id, workflow_id, status='failed',
                            reason='Design action could not be started. Inspect the operation before retrying.')
            raise

    @app.get("/api/target-selections/{workflow_id}/{target_id}")
    def get_selection(workflow_id: str, target_id: str):
        index = service.require()
        package = index.model["package"]["sha256"]
        if not re.fullmatch(r"[a-f0-9]{64}", workflow_id) or not re.fullmatch(r"[a-f0-9]{64}", target_id):
            raise HTTPException(404, "Unknown target selection")
        service.workflow(index, workflow_id)
        with _db(service) as db:
            row = db.execute("SELECT payload FROM target_selections WHERE package_hash=? AND workflow_id=? AND target_id=?", (package, workflow_id, target_id)).fetchone() if db else None
        if not row:
            return {"version": 0, "selection": None}
        saved = json.loads(row["payload"])
        try:
            _target(index, workflow_id, saved["scope"], saved["input_version"], target_id)
            saved["stale"] = False
        except HTTPException as error:
            if error.status_code not in (404, 409):
                raise
            saved["stale"] = True
        return {"version": saved["version"], "selection": saved}

    @app.post("/api/target-selections/{target_id}")
    def save_selection(target_id: str, body: TargetRequest):
        if not re.fullmatch(r"[a-f0-9]{64}", target_id):
            raise HTTPException(404, "Unknown target")
        index = service.require()
        _target(index, body.workflow_id, body.scope, body.input_version, target_id)
        catalog, schema, table = map(_identifier, (body.catalog, body.schema_name, body.table))
        package = index.model["package"]["sha256"]
        value = {**body.model_dump(exclude={"expected_version"}), "target_id": target_id,
                 "package_hash": package, "version": body.expected_version + 1,
                 "qualified_name": ".".join(f"`{part}`" for part in (catalog, schema, table)),
                 "metadata_verified": False, "schema_compatible": None, "approved": False,
                 "execution_allowed": False, "deployment_allowed": False}
        ds._safe(_json(value).encode())
        with _db(service, write=True) as db:
            row = db.execute("SELECT version FROM target_selections WHERE package_hash=? AND workflow_id=? AND target_id=?", (package, body.workflow_id, target_id)).fetchone()
            if (row["version"] if row else 0) != body.expected_version:
                raise HTTPException(409, "Target selection changed; reload before saving")
            db.execute("INSERT OR REPLACE INTO target_selections VALUES (?,?,?,?,?)",
                       (package, body.workflow_id, target_id, value["version"], _json(value)))
        return {"version": value["version"], "selection": value}

    @app.post("/api/target-selections/{target_id}/verify")
    def verify_selection(target_id: str, body: TargetVerification):
        from databricks.sdk.errors import NotFound
        from workbench.databricks_models import AuthenticationUnavailable, RoutingError
        from workbench.fixture_metadata import _column, _signature
        from workbench.target_fixtures import target_table

        if not body.consent:
            raise HTTPException(403, "Explicit consent is required for the metadata read")
        selected = get_selection(body.workflow_id, target_id)
        value = selected['selection']
        if not value or selected['version'] != body.expected_version or value['stale']:
            raise HTTPException(409, "Target selection changed or is stale; reload it before verification")
        index = service.require()
        inventory = _target(index, body.workflow_id, value['scope'], value['input_version'], target_id)
        target = next(row for row in inventory['targets']['items'] if row['target_id'] == target_id)
        fields = target['columns']
        try:
            if not fields or len(fields) > 1000:
                raise ValueError("Missing or excessive target fields")
            planned = target_table(target['exported_identity'], fields, target['source_scope'])
            expected = [{key: column[key] for key in ('name', 'type', 'nullable')}
                        for column in planned['columns']]
            if (any(not isinstance(c['name'], str) or not c['name'] for c in expected)
                    or len({c['name'].casefold() for c in expected}) != len(expected)):
                raise ValueError("Invalid target fields")
        except (ValueError, KeyError, TypeError):
            raise HTTPException(409, "Canonical target fields or types are incomplete; compatibility cannot be verified") from None
        try:
            identity = app.state.fixture_identity_provider(None)
        except (AuthenticationUnavailable, RoutingError):
            raise HTTPException(401, "Workspace authentication failed; no alternate identity used") from None
        result = {'target_id': target_id, 'workflow_id': body.workflow_id,
                  'package_hash': value['package_hash'], 'selection_version': value['version'],
                  'input_version': value['input_version'], 'qualified_name': value['qualified_name'],
                  'identity': {'mode': identity.mode, 'host': identity.host, 'user_id': identity.user_id},
                  'observed_at': datetime.now(timezone.utc).isoformat(), 'metadata_verified': False,
                  'schema_compatible': None, 'expected_columns': expected, 'columns': [],
                  'approved': False, 'execution_allowed': False, 'deployment_allowed': False,
                  'semantic_parity': 'not_established', 'is_test_fixture': value['is_test_fixture'],
                  'verification_persisted': False, 'data_access_verified': False,
                  'data_access_status': 'not_tested',
                  'notice': 'Metadata only; SELECT and write permissions, data contents and production binding are not verified.'}
        schema_name = value['catalog'] + '.' + value['schema_name']
        table_name = schema_name + '.' + value['table']
        try:
            schema = identity.client.schemas.get(schema_name)
            if schema.full_name != schema_name:
                raise ValueError('Schema identity mismatch')
            table = identity.client.tables.get(table_name)
            if table.full_name != table_name or len(table.columns or []) > 1000:
                raise ValueError('Table identity or column bound mismatch')
            columns = [_column(column) for column in table.columns or []]
            if any(not isinstance(c['name'], str) or not isinstance(c['type'], str)
                   or type(c['nullable']) is not bool for c in columns):
                raise ValueError('Incomplete table metadata')
            result.update(status='metadata_observed', metadata_verified=True, columns=columns,
                          schema_compatible=_signature(columns) == _signature(expected))
        except NotFound:
            result.update(status='not_found')
        except Exception as error:
            result.update(status='metadata_error', error=type(error).__name__)
        if body.check_select and result['metadata_verified']:
            warehouse = os.environ.get('WORKBENCH_SQL_WAREHOUSE_ID', '')
            if not warehouse:
                result['data_access_status'] = 'warehouse_not_configured'
            else:
                try:
                    me = identity.client.current_user.me()
                    if (me.id != identity.user_id or not me.user_name
                            or identity.client.config.host.rstrip('/').lower() != identity.host.rstrip('/').lower()):
                        raise PermissionError('Request identity mismatch')
                    store = ds.DeltaStateStore(identity.client, warehouse, value['catalog'], value['schema_name'], identity.user_id)
                    store._execute('SELECT 1 FROM ' + value['qualified_name'] + ' WHERE 1=0')
                    result.update(data_access_verified=True, data_access_status='select_zero_row_probe_succeeded',
                                  notice='Zero-row SELECT succeeded as the requesting user. Data contents, write permissions and production binding are not verified.')
                except Exception:
                    result['data_access_status'] = 'select_probe_unavailable_or_denied'
        latest = get_selection(body.workflow_id, target_id)
        if (latest['version'] != body.expected_version or not latest['selection']
                or latest['selection']['stale']):
            raise HTTPException(409, "Target selection changed during verification; discard the observation")
        return result