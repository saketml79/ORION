"""Explicit, fresh candidate-only writer jobs for terminal partial agent runs."""
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool


class CandidateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    consent: StrictBool
    design_id: str = Field(pattern=r'^[a-f0-9]{64}$')
    version: int = Field(ge=1)
    current_input_version: str = Field(pattern=r'^[a-f0-9]{64}$')


def validate_request(service, parent_id, request):
    """Prove ownership against the exact original or sealed revision submission."""
    from workbench.redesign import store_for
    from workbench.spark_codegen import ContractError, _approved
    if request.consent is not True:
        raise HTTPException(403, 'Explicit consent:true is required for candidate-only drafting')
    index = service.require()
    parent = service.record(parent_id, agent=True)
    package = index.model['package']['sha256']
    if parent.get('package_hash') != package:
        raise HTTPException(404, 'Unknown parent run in this package')
    if (parent.get('id') != parent_id or parent.get('status') != 'partial'
            or parent.get('consent') is not True or parent.get('generate_code') is not True):
        raise HTTPException(409, 'Candidate-only retry requires a terminal partial parent that requested code')
    try:
        saved = store_for(service).get(request.design_id, package)
    except KeyError:
        raise HTTPException(404, 'Unknown design in this package') from None
    scope = ('package_hash', 'workflow_id', 'scenario', 'input_version')
    source = saved.get('source', {})
    try:
        from workbench.design_revision import _source_runtime
        selected = _source_runtime(parent, saved, service.root)
    except Exception:
        selected = None
    source_round, output = selected if selected is not None else (None, {})
    submissions = output.get('redesigns') or []
    number = source.get('submission_index')
    submission = (submissions[number] if type(number) is int and isinstance(submissions, list)
                  and 0 <= number < len(submissions) else None)
    if (saved['version'] != request.version
            or {'id': saved['id'], 'version': request.version} not in parent.get('redesign_refs', [])
            or any(saved.get(key) != parent.get(key) for key in scope)
            or source.get('kind') != 'agent' or source.get('record_id') != parent_id
            or not output.get('run_id') or source.get('runtime_run_id') != output.get('run_id')
            or source.get('runtime_input_hashes') != output.get('input_hashes')
            or source.get('input_version') != parent.get('input_version')
            or any(source.get(key) != parent.get(key) for key in ('input_hashes', 'input_versions'))
            or not isinstance(submission, dict)
            or submission.get('role') not in {'architecture', 'supervisor'}
            or submission.get('role') != saved.get('author_role')
            or submission.get('design') != saved['design']
            or submission.get('retrieved_evidence_ids') != saved.get('retrieved_evidence_ids')
            or (source_round in (1, 2) and (submission.get('design_origin') != 'agent_submitted'
                or source.get('design_origin') != 'agent_submitted'))):
        raise HTTPException(409, 'Exact latest design is not proven by this parent typed submission')
    inputs = service.agent_inputs(index, saved['workflow_id'], saved['scenario'])
    changed = [key for key in ('input_hashes', 'input_versions') if inputs[key] != source.get(key)]
    if (request.current_input_version != inputs['input_version']
            or saved['input_version'] != inputs['input_version'] or changed):
        raise HTTPException(409, {
            'code': 'candidate_design_stale', 'design_id': saved['id'], 'version': saved['version'],
            'design_input_version': saved['input_version'], 'current_input_version': inputs['input_version'],
            'changed_pins': changed,
            'action': 'Refresh the exact saved design. If it is stale, explicitly regenerate or revalidate its design inputs before retrying. Candidate consent cannot re-pin an old design.',
            'approved': False, 'execution_allowed': False, 'deployment_allowed': False})
    try:
        _approved(service, saved, require_review=False)
    except ContractError as error:
        raise HTTPException(409, str(error)) from None
    return parent, saved


def validate_authority(service, design, parent_id, authority):
    """A persisted separate consented job is mandatory, including at commit time."""
    from workbench.spark_codegen import ContractError
    if not isinstance(authority, dict) or set(authority) != {'job_id', 'parent_run_id', 'request'}:
        raise ContractError('Invalid candidate retry authority')
    job = service.record(authority['job_id'])
    if (authority['parent_run_id'] != parent_id or job.get('candidate_authority') != authority
            or job.get('id') != authority['job_id'] or job.get('kind') != 'spark_draft'
            or job.get('status') not in {'queued', 'running'} or job.get('consent') is not True
            or job.get('agent_run_id') != parent_id or job.get('approved') is not False
            or job.get('execution_allowed') is not False or job.get('deployment_allowed') is not False
            or job.get('design_id') != design['id'] or job.get('design_version') != design['version']
            or any(job.get(key) != design[key] for key in ('package_hash', 'workflow_id', 'scenario', 'input_version'))):
        raise ContractError('Candidate retry authority does not match the separate consented writer job')
    _, saved = validate_request(service, parent_id, CandidateRequest.model_validate(authority['request']))
    if any(saved[key] != design[key] for key in ('id', 'version', 'design', 'input_version')):
        raise ContractError('Candidate retry design changed')


def _now():
    return datetime.now(timezone.utc).isoformat()


def run_candidate(service, initial, saved):
    """One new invocation. No analysis runtime, parent updates or checkpoint resume."""
    from workbench.app import _digest
    from workbench.spark_codegen import draft_agent_design
    job = dict(initial)
    try:
        job.update(status='running', started_at=_now(), phase='candidate_code_generation')
        service.update_record(job)
        draft = draft_agent_design(service, saved, agent_run_id=job['agent_run_id'], consent=True,
                                   candidate_authority=job['candidate_authority'])
        job.update(status='completed', manifest_sha256=_digest(
            service.root / draft['relative_directory'] / 'manifest.json'), output={
                key: draft.get(key) for key in ('run_id', 'relative_directory', 'status', 'failure',
                                               'failure_diagnostic', 'validation', 'writer_skill_receipt')})
        from workbench.domain import _redact
        job['output']['design_correction_feedback'] = _redact(draft.get('design_correction_feedback'))
        job['output'].update(manifest_status=draft['status'], artifact_approved=False,
                             execution_allowed=False, deployment_allowed=False,
                             task_statuses=[{key: task.get(key) for key in
                                 ('task_id', 'status', 'artifact_paths', 'blockers')}
                                 for task in draft.get('tasks', [])])
    except Exception as error:
        from workbench.agent_recovery import safe_exception_class
        from workbench.databricks_models import safe_failure_diagnostic
        job.update(status='failed', failure_stage='candidate_code_generation',
                   exception_class=safe_exception_class(error),
                   failure_diagnostic=safe_failure_diagnostic(error, 'writer_invocation'),
                   error='Candidate writer failed. Parent history and approvals are unchanged.')
    finally:
        try:
            job.update(finished_at=_now(), phase='complete')
            service.update_record(job)
        finally:
            service.agent_slot.release()


def install_candidate_retry(app):
    service = app.state.service

    @app.post('/api/agents/runs/{run_id}/candidate-code', status_code=202)
    def candidate_code(run_id: str, body: CandidateRequest):
        if body.consent is not True:
            raise HTTPException(403, 'Explicit consent:true is required for candidate-only drafting')
        if not service.agent_slot.acquire(blocking=False):
            raise HTTPException(409, 'An agent or writer is already running; no retry was queued')
        try:
            with service.lock:
                parent, saved = validate_request(service, run_id, body)
                from workbench.model_access import workspace_ready
                from langchain_core.language_models import BaseChatModel
                routes = getattr(service.runtime, 'role_models', None)
                if (not workspace_ready(service.runtime) or not isinstance(routes, dict)
                        or not isinstance(routes.get('artifact'), BaseChatModel)):
                    raise HTTPException(409, 'Connect and select the configured artifact model before candidate-only retry')
                job_id = uuid4().hex
                authority = {'job_id': job_id, 'parent_run_id': run_id, 'request': body.model_dump()}
                job = {'id': job_id, 'kind': 'spark_draft', 'status': 'queued', 'created_at': _now(),
                       'consent': True, 'agent_run_id': parent['id'], 'design_id': saved['id'],
                       'design_version': saved['version'], 'candidate_authority': authority,
                       **{key: saved[key] for key in ('package_hash', 'workflow_id', 'scenario', 'input_version')},
                       'advisory_only': True, 'approved': False,
                       'execution_allowed': False, 'deployment_allowed': False}
                service.update_record(job)
            try:
                service.agent_executor.submit(run_candidate, service, job, saved)
            except Exception:
                service.update_record({**job, 'status': 'failed', 'finished_at': _now(),
                                       'error': 'Candidate writer could not be scheduled; no inference started.'})
                raise
        except Exception:
            service.agent_slot.release()
            raise
        return job