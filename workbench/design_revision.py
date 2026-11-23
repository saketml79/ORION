"""Prepare an exact review-to-revision handoff, without invoking a revision.

The handoff is an index into the writer's immutable manifest, not an assertion
that a design issue is correct or that an agent has acted on it.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json

from workbench.generated_code_review import PartitionReviewResponse, ReviewIssue


class RevisionContextError(ValueError):
    """A verified handoff cannot safely fit in an exact revision context."""

    def __init__(self, status, public_reason):
        super().__init__(public_reason)
        self.status = status
        self.public_reason = public_reason


def _digest(value):
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=True,
                         allow_nan=False, separators=(',', ':'))
    return sha256(encoded.encode()).hexdigest()


def _source_runtime(parent, design, root=None):
    """Resolve the design's explicit source, never a caller-provided output path."""
    source = design['source']
    original = parent['output']
    runtime_id = source.get('runtime_run_id')
    if not isinstance(runtime_id, str) or not runtime_id:
        return None
    if runtime_id == original.get('run_id'):
        return 0, original
    if root is None:
        return None
    from workbench.app import _revision_runtime_provenance
    resolved = _revision_runtime_provenance(root, parent, runtime_id)
    if resolved is None:
        return None
    entry, output, _, _ = resolved
    if (entry.get('round_index') not in (1, 2) or entry.get('status') != 'draft_available'
            or not isinstance(output, dict)):
        return None
    return entry['round_index'], output


def prepare_revision_handoff(parent, design, inputs, writer_run_id, review, value, bodies, *, root=None):
    """Index bounded, typed design opinions for the exact committed candidate.

    Round zero is the original writer/review, not a revision attempt. Only a
    fully reviewed task with an explicitly attributed design issue is eligible.
    Missing or inconsistent proof produces no handoff and never changes drafting.
    """
    source = design['source']
    selected = _source_runtime(parent, design, root)
    if selected is None:
        return None
    source_round, output = selected
    number = source['submission_index']
    submissions = output['redesigns']
    if (parent.get('consent') is not True or parent.get('generate_code') is not True
            or source.get('kind') != 'agent' or source.get('record_id') != parent.get('id')
            or source.get('runtime_run_id') != output.get('run_id')
            or source.get('runtime_input_hashes') != output.get('input_hashes')
            or any(parent.get(k) != design.get(k) for k in
                   ('package_hash', 'workflow_id', 'scenario', 'input_version'))
            or any(parent.get(k) != inputs.get(k) or source.get(k) != inputs.get(k)
                   for k in ('input_hashes', 'input_versions'))
            or source.get('input_version') != inputs.get('input_version')
            or type(number) is not int or not isinstance(submissions, list)
            or not 0 <= number < len(submissions)):
        return None
    submission = submissions[number]
    author = design['author_role']
    if (author not in {'architecture', 'supervisor'} or not isinstance(submission, dict)
            or submission.get('role') != author or submission.get('design') != design['design']
            or submission.get('retrieved_evidence_ids') != design.get('retrieved_evidence_ids')
            or (source_round in (1, 2) and (submission.get('design_origin') != 'agent_submitted'
                or source.get('design_origin') != 'agent_submitted'))):
        return None

    artifacts = [dict(relative_path=a.relative_path, task_ids=sorted(a.task_ids),
                      content_sha256=sha256(bodies[a.relative_path].encode()).hexdigest())
                 for a in sorted(value.artifacts, key=lambda a: a.relative_path)]
    if (review.get('candidate_sha256') != _digest(artifacts)
            or value.design_id != design['id'] or value.design_version != design['version']
            or value.design_sha256 != _digest(design['design'])
            or value.input_version != inputs['input_version']
            or value.package_hash != design['package_hash']):
        return None
    tasks = review['tasks']
    if (not isinstance(tasks, list) or len({task['task_id'] for task in tasks}) != len(tasks)
            or review.get('scope_sha256') !=
            _digest([task['scope_sha256'] for task in tasks])):
        return None
    task_ids = {task['id'] for task in design['design']['tasks']}
    references = []
    for task_index, task in enumerate(tasks):
        task_id = task['task_id']
        expected_files = {a['relative_path']: a['content_sha256'] for a in artifacts
                          if task_id in a['task_ids']}
        if (task_id not in task_ids or task.get('ai_reviewed') is not True
                or task.get('source_complete') is not True
                or task.get('artifact_hashes') != expected_files):
            continue
        partitions = task.get('partition_reviews')
        if isinstance(partitions, list):
            count = task.get('partition_count')
            coverage = task.get('coverage_manifest')
            plan = task.get('partition_plan')
            if (task.get('partition_review_complete') is not True
                    or task.get('synthesis_reviewed') is not True
                    or type(count) is not int or count < 1 or len(partitions) != count + 1
                    or not isinstance(coverage, dict) or coverage.get('source_complete') is not True
                    or not isinstance(plan, list) or len(plan) != count
                    or any(not isinstance(part, list) or not part for part in plan)
                    or [item for part in plan for item in part] != coverage.get('records')
                    or task.get('coverage_manifest_sha256') != _digest(coverage)):
                return None
            collections = []
            for partition_index, partition in enumerate(partitions):
                payload = {k: v for k, v in partition.items() if k != 'receipt_sha256'}
                if (partition.get('receipt_sha256') != _digest(payload)
                        or PartitionReviewResponse.model_validate(payload).task_id != task_id
                        or partition.get('scope_sha256') != task.get('scope_sha256')
                        or partition.get('partition_count') != count
                        or partition.get('partition_index') != partition_index
                        or partition.get('phase') != ('final_synthesis' if partition_index == count
                                                      else 'evidence_partition')):
                    return None
                collections.append((partition_index, partition['issues']))
        else:
            if 'partition_plan' in task or 'partition_reviews' in task:
                return None
            collections = [(None, task.get('reviewer_issues', []))]
        for partition_index, issues in collections:
            for issue_index, raw in enumerate(issues):
                issue = ReviewIssue.model_validate(raw)
                if (issue.task_id != task_id or issue.relative_path is not None
                        and issue.relative_path not in expected_files):
                    return None
                if (issue.issue_origin != 'design' or issue.responsible_agent not in
                        {'architecture', 'supervisor', 'semantics', 'discovery', 'validation', 'artifact'}):
                    continue
                references.append({'task_index': task_index, 'partition_index': partition_index,
                    'issue_index': issue_index, 'issue_sha256': _digest(issue.model_dump()),
                    'task_scope_sha256': task['scope_sha256'],
                    'responsible_agent': issue.responsible_agent})
                if len(references) > 128:
                    raise RevisionContextError('context_limit',
                        'More than 128 eligible design issues cannot fit in one exact revision context; no revision was started.')
    if not references:
        return None
    return {'schema_version': 1, 'source_round': source_round, 'parent_run_id': parent['id'],
            'runtime_run_id': source['runtime_run_id'], 'submission_index': number,
            'submission_sha256': _digest(submission),
            'writer_run_id': writer_run_id, 'design_id': design['id'],
            'design_version': design['version'], 'design_sha256': _digest(design['design']),
            'design_author': author, 'package_hash': design['package_hash'],
            'workflow_id': design['workflow_id'], 'scenario': design['scenario'],
            'input_version': inputs['input_version'], 'input_hashes': inputs['input_hashes'],
            'input_versions': inputs['input_versions'], 'runtime_input_hashes': output['input_hashes'],
            'review_sha256': _digest(review), 'review_scope_sha256': review['scope_sha256'],
            'candidate_sha256': review['candidate_sha256'], 'feedback_references': references}


def resolve_revision_feedback(handoff, review):
    """Resolve exact typed opinions from the same manifest, rechecking hashes.

    Callers must separately revalidate consent, project/package, current inputs,
    source submission, and manifest integrity before using these opinions.
    """
    if (handoff['review_sha256'] != _digest(review)
            or handoff['review_scope_sha256'] != review['scope_sha256']
            or handoff['candidate_sha256'] != review['candidate_sha256']
            or not 1 <= len(handoff['feedback_references']) <= 128):
        raise ValueError('Revision feedback does not match the recorded review')
    resolved = []
    for ref in handoff['feedback_references']:
        task = review['tasks'][ref['task_index']]
        partition_index = ref['partition_index']
        if partition_index is None:
            issues = task['reviewer_issues']
        else:
            partition = task['partition_reviews'][partition_index]
            payload = {k: v for k, v in partition.items() if k != 'receipt_sha256'}
            if partition['receipt_sha256'] != _digest(payload):
                raise ValueError('Revision partition receipt changed')
            PartitionReviewResponse.model_validate(payload)
            issues = partition['issues']
        issue = ReviewIssue.model_validate(issues[ref['issue_index']])
        if (task.get('source_complete') is not True or task.get('ai_reviewed') is not True
            or task['scope_sha256'] != ref['task_scope_sha256']
                or issue.task_id != task['task_id'] or issue.issue_origin != 'design'
                or issue.responsible_agent != ref['responsible_agent']
                or _digest(issue.model_dump()) != ref['issue_sha256']):
            raise ValueError('Revision feedback reference changed')
        resolved.append(issue)
    return resolved


def capture_revision_author(service, index, record, output, submission_index, author):
    """Validate and append exactly one author submission with its original index."""
    import re
    from workbench.agents import _Evidence
    from workbench.redesign import store_for, validate_submission
    from workbench.retrieval_receipts import replay_planning

    submissions = output.get('redesigns')
    runtime_id = output.get('run_id')
    if (record.get('consent') is not True or output.get('status') != 'draft_available'
            or not isinstance(submissions, list) or not 1 <= len(submissions) <= 10
            or type(submission_index) is not int or not 0 <= submission_index < len(submissions)
            or not isinstance(runtime_id, str) or not re.fullmatch(r'[a-f0-9]{32}', runtime_id)
            or author not in {'architecture', 'supervisor'}
            or not isinstance(record.get('redesign_snapshot'), dict)):
        raise ValueError('Invalid revision author receipt')
    submission = submissions[submission_index]
    required = {'role', 'design', 'retrieved_evidence_ids', 'design_origin'}
    if (not isinstance(submission, dict) or set(submission) != required
            or submission['role'] != author or submission['design_origin'] != 'agent_submitted'):
        raise ValueError('Invalid chosen revision submission')
    captured = record['redesign_snapshot']
    evidence = _Evidence(index.model, record['workflow_id'], captured['documents'], captured['reviews'])
    for page in output.get('neighbor_pages', []):
        evidence.package_neighbors(page['offset'], page['limit'])
    role_records = replay_planning(index.model, record['workflow_id'],
                                   output.get('planning_retrievals', []), evidence.records)
    design = validate_submission(submission['design'], index.model, record['workflow_id'],
                                 submission['retrieved_evidence_ids'],
                                 role_records.get(author, evidence.records), scenario=record['scenario'])
    normalized = {**submission, 'design': design.model_dump()}
    if normalized['design'] != submission['design']:
        raise ValueError('Chosen revision submission does not match its normalized design')
    source = {'record_id': record['id'], 'runtime_run_id': runtime_id,
              'submission_index': submission_index, 'kind': 'agent',
              'input_version': record['input_version'],
              'base_input_version': record.get('base_input_version', record['input_version']),
              'input_hashes': record['input_hashes'], 'input_versions': record['input_versions'],
              'runtime_input_hashes': output.get('input_hashes', {}), 'design_origin': 'agent_submitted'}
    return store_for(service).append(record['package_hash'], record['workflow_id'], record['scenario'],
                                     normalized, source, record.get('redesign_expected_version', 0))


def _verify_committed_handoff(service, index, parent_id, job_id, snapshot, *, internal):
    """Verify a committed handoff and return an internal-only orchestration result.

    Reconstruct only artifact ownership from the committed task report. The
    writer validated that ownership against its artifact set before commit;
    the handoff's candidate digest and the bounded reader verify it again.
    On success, ``internal`` contains sensitive review/design data and must not
    be serialized into public responses. This observation is not an admission
    lock, consent to a new run, or authority to execute or deploy anything.
    """
    from types import SimpleNamespace
    from workbench.draft_artifact_reads import read_result
    from workbench.redesign import store_for

    unavailable = {'status': 'unavailable', 'revision_started': False,
                   'reason': 'Exact committed review or source pins could not be verified.',
                   'execution_allowed': False, 'deployment_allowed': False}
    try:
        parent = service.record(parent_id, agent=True)
        job = service.record(job_id)
        active = service.require()
        if (active.model['package']['sha256'] != index.model['package']['sha256']
                or parent.get('id') != parent_id or job.get('id') != job_id
                or job.get('agent_run_id') != parent_id or job.get('status') != 'completed'
                or any(job.get(k) != parent.get(k) for k in
                       ('package_hash', 'workflow_id', 'scenario', 'input_version'))):
            return unavailable
        current_inputs = service.agent_inputs(index, parent['workflow_id'], parent['scenario'])
        if (current_inputs.get('snapshot') != snapshot or any(current_inputs.get(k) != parent.get(k) for k in
                ('input_version', 'input_hashes', 'input_versions'))):
            return unavailable
        inputs = {k: current_inputs[k] for k in ('input_version', 'input_hashes', 'input_versions')}
        store = store_for(service)
        design = store.get(job['design_id'], parent['package_hash'], job['design_version'])
        latest = store.get(job['design_id'], parent['package_hash'])
        if (latest['version'] != design['version'] or
                {'id': design['id'], 'version': design['version']} not in parent.get('redesign_refs', [])):
            return unavailable
        decisions = [r for r in design['reviews'] if r['action'] != 'comment']
        if decisions and decisions[-1]['action'] in {'reject', 'request_changes'}:
            return unavailable
        saved = read_result(service.root, job, job_id)
        manifest, bodies = saved['manifest'], saved['artifacts']
        handoff, review = manifest.get('design_revision_handoff'), manifest.get('generated_code_review')
        if not isinstance(review, dict):
            return unavailable
        selected = _source_runtime(parent, design, service.root)
        if selected is None:
            return unavailable
        source_round, source_output = selected
        ledger = parent.get('revision_ledger')
        # A previously admitted revision cannot be replayed, and a round-two
        # result cannot seed a third revision. Require an exact sequential chain.
        if ledger is None and source_round != 0:
            return unavailable
        if ledger is not None:
            if (not isinstance(ledger, dict)
                    or set(ledger) != {'schema_version', 'current_runtime_run_id', 'entries'}
                    or type(ledger.get('schema_version')) is not int or ledger['schema_version'] != 1
                    or not isinstance(ledger.get('entries'), list)
                    or len(ledger['entries']) != source_round
                    or (source_round == 0 and ledger.get('current_runtime_run_id') is not None)
                    or (source_round in (1, 2) and ledger.get('current_runtime_run_id') not in
                        (None, source_output['run_id']))):
                return unavailable
        if (manifest.get('input_hashes') != inputs['input_hashes']
                or manifest.get('input_versions') != inputs['input_versions']):
            return unavailable
        tasks = manifest['tasks']
        if not isinstance(tasks, list) or len({t['task_id'] for t in tasks}) != len(tasks):
            return unavailable
        owners = {path: [] for path in bodies}
        for task in tasks:
            paths = task['artifact_paths']
            if not isinstance(paths, list) or len(set(paths)) != len(paths):
                return unavailable
            for path in paths:
                if path not in owners:
                    return unavailable
                owners[path].append(task['task_id'])
        if any(not task_ids for task_ids in owners.values()):
            return unavailable
        value = SimpleNamespace(
            artifacts=[SimpleNamespace(relative_path=path, task_ids=task_ids)
                       for path, task_ids in owners.items()],
            **{k: manifest[k] for k in ('design_id', 'design_version', 'design_sha256',
                                         'input_version', 'package_hash')})
        rebuilt = prepare_revision_handoff(parent, design, inputs, manifest['run_id'], review, value, bodies,
                                           root=service.root)
        if rebuilt is None or (handoff is not None and rebuilt != handoff):
            return unavailable
        # Existing writers do not yet pass root to preparation. Only for a
        # sealed round-one source, derive the missing index from the verified
        # immutable manifest; round zero retains its existing requirement.
        if handoff is None:
            if source_round not in (1, 2) or 'design_revision_handoff' in manifest:
                return unavailable
            handoff = rebuilt
        if not isinstance(handoff, dict):
            return unavailable
        issues = resolve_revision_feedback(handoff, review)
        # This is an observation, not an admission lock. Recheck after the
        # bounded file reads; any future runtime must repeat these checks at
        # its own admission boundary.
        current = service.agent_inputs(index, parent['workflow_id'], parent['scenario'])
        current_parent = service.record(parent_id, agent=True)
        current_job = service.record(job_id)
        if (service.require().model['package']['sha256'] != parent['package_hash']
                or current.get('snapshot') != snapshot
                or any(current.get(k) != inputs.get(k) for k in
                       ('input_version', 'input_hashes', 'input_versions'))
                or any(current_parent.get(k) != parent.get(k) for k in
                      ('output', 'revision_ledger', 'redesign_refs', 'consent', 'generate_code', 'input_version',
                        'input_hashes', 'input_versions', 'package_hash', 'workflow_id', 'scenario'))
                or current_job != job
                                or store.get(design['id'], parent['package_hash']) != latest
                                or store.get(design['id'], parent['package_hash'], design['version']) != design
                                or _source_runtime(current_parent, design, service.root) != selected):
            return unavailable
        roles = {issue.responsible_agent for issue in issues}
        if source_round == 2:
            return {'status': 'revision_limit_reached', 'revision_started': False,
                'reason': 'Verified eligible design feedback remains after two revision rounds.',
                'issue_count': len(issues), 'execution_allowed': False, 'deployment_allowed': False}
        if len(roles) != 1:
            return {'status': 'ambiguous_role', 'revision_started': False,
                    'reason': 'The exact review attributes design issues to multiple roles; no role was selected.',
                    'execution_allowed': False, 'deployment_allowed': False}
        role = next(iter(roles))
        summary = {'status': 'verified_not_started', 'revision_started': False,
            'reason': 'Feedback was verified at inspection time; admission must recheck the same committed pins.',
            'responsible_agent': role, 'issue_count': len(issues),
            'writer_job_id': job_id, 'writer_run_id': manifest['run_id'],
            'manifest_sha256': job['manifest_sha256'], 'review_sha256': handoff['review_sha256'],
            'design_id': design['id'], 'design_version': design['version'],
            'runtime_run_id': handoff['runtime_run_id'],
            'execution_allowed': False, 'deployment_allowed': False}
        from workbench.agents import _revision_context
        revision_context = _revision_context({
            'responsible_agent': role, 'prior_design': design['design'],
            'review_issues': [issue.model_dump() for issue in issues],
            'round_index': source_round + 1,
            'original_runtime_run_id': parent['output']['run_id'],
            'design_id': design['id'], 'design_version': design['version']})
        if not internal:
            return summary
        return {**summary,
            'internal': {'source_round': source_round, 'next_round': source_round + 1,
                    'review_issues': issues, 'selected_design': deepcopy(design),
                    'source_runtime_run_id': handoff['runtime_run_id'],
                    'original_runtime_run_id': parent['output']['run_id'],
                    'runtime_input_hashes': deepcopy(handoff['runtime_input_hashes']),
                    'fresh_pins': deepcopy(inputs), 'manifest_sha256': job['manifest_sha256'],
                    'handoff': deepcopy(handoff), 'revision_context': revision_context}}
    except RevisionContextError as error:
        return {'status': error.status, 'revision_started': False,
                'reason': error.public_reason, 'execution_allowed': False, 'deployment_allowed': False}
    except Exception:
        # A failed preflight must not alter, replace, or invalidate the draft.
        return unavailable


def verify_committed_handoff(service, index, parent_id, job_id, snapshot):
    """Return the summary and, on success, sensitive internal orchestration data.

    The internal result must never be exposed as a public response. It is an
    observation only; admission must reverify it under its own consent and lock.
    """
    return _verify_committed_handoff(service, index, parent_id, job_id, snapshot, internal=True)


def inspect_committed_handoff(service, index, parent_id, job_id, snapshot):
    """Return only the public, non-content-bearing verification summary."""
    return _verify_committed_handoff(service, index, parent_id, job_id, snapshot, internal=False)