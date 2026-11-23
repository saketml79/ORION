"""Nonexecuting supplemental source evidence, never executable dependency edges.

Java views retain noncredential comments and literal values. Only a narrow,
literal-plus-identifier SQL assignment grammar is exposed separately, as a Java
expression, not SQL suitable for translation. Unsupported syntax fails closed.
"""
from collections import defaultdict
from hashlib import sha256
import re
import shlex
from workbench.redaction import redact_text


_JAVA_TOKEN = re.compile(r'//[^\r\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\\r\n])*"|\'(?:\\.|[^\'\\\r\n])*\'')
_SQL_ASSIGNMENT = re.compile(
    r'\b(?P<variable>[A-Za-z_$][\w$]*)\s*=\s*(?P<expression>"[^"\\\r\n]*"'
    r'(?:\s*\+\s*(?:"[^"\\\r\n]*"|[A-Za-z_$][\w$]*))*)\s*;')
_IDENTIFIER = re.compile(r'[A-Za-z_][A-Za-z0-9_.-]*\Z')


def embedded_java(value):
    """Return a line-preserving structural view, without recovering credentials.

    Original canonical VALUE redaction is unchanged. This supplemental view is
    not certified compilable Java. Only credential-sanitized source is returned.
    """
    result = {'language': 'java', 'source_sha256': sha256(value.encode()).hexdigest(),
              'source_characters': len(value), 'source_lines': len(value.splitlines()),
              'status': 'sanitized_structural_view', 'sql_expressions': [],
              'literal_values': 'noncredential_values_preserved', 'executable': False}
    # Text blocks and unterminated strings/comments are outside this lexer.
    if '"""' in value or re.search(r'\\u[0-9a-fA-F]{4}', value):
        return {**result, 'status': 'unsupported_java_lexical_form', 'structural_source': '[REDACTED]'}
    bare = _JAVA_TOKEN.sub(lambda m: '\n' * m.group().count('\n'), value)
    if '"' in bare or "'" in bare or '/*' in bare:
        return {**result, 'status': 'unsupported_java_lexical_form', 'structural_source': '[REDACTED]'}
    # Remove comments before selecting SQL, so commented examples are not code.
    uncommented = _JAVA_TOKEN.sub(
        lambda m: ('\n' * m.group().count('\n') if m.group().startswith(('/',)) else m.group()), value)
    for match in _SQL_ASSIGNMENT.finditer(uncommented):
        expression = match['expression']
        if re.match(r'"\s*(?:select|with|insert|update|delete|call)\b', expression, re.I):
            result['sql_expressions'].append({
            'variable': match['variable'], 'expression': redact_text(expression),
                'line': uncommented.count('\n', 0, match.start()) + 1,
                'status': 'dynamic_java_expression_not_standalone_sql',
                'translation_allowed': False})
    # Mask only credential values, never erase the surrounding Java statement.
    result['structural_source'] = redact_text(value)
    return result


def workflow_invocations(model):
    """Literal pmcmd startworkflow candidates, not scheduler/return dependencies.

    The Integration Service's runtime repository cannot be verified from this
    export. Candidate matching is exact within the caller's export repository
    and the literal -f folder. No global name fallback or cross-repository guess.
    """
    nodes = {n['id']: n for n in model['nodes']}
    reverse = defaultdict(list)
    for edge in model['edges']:
        if edge['kind'] in {'contains', 'task_definition', 'shortcut_reference', 'component_reference'}:
            reverse[edge['target']].append(edge['source'])
    workflows = defaultdict(list)
    for node in nodes.values():
        if node['kind'] == 'workflow':
            workflows[(node.get('scope', {}).get('repository'),
                       node.get('scope', {}).get('folder'), node['name'])].append(node['id'])
    rows = []
    for node in nodes.values():
        if node['kind'] != 'task' or node.get('attributes', {}).get('TYPE', '').lower() != 'command':
            continue
        callers, seen, pending = set(), set(), [node['id']]
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            if nodes[current]['kind'] == 'workflow':
                callers.add(current)
            else:
                pending.extend(reverse.get(current, ()))
        for ordinal, prop in enumerate(node.get('properties', [])):
            command = prop.get('attributes', {}).get('VALUE', '')
            if not re.search(r'\bpmcmd\s+startworkflow\b', command, re.I):
                continue
            parsed = _pmcmd(command)
            for caller in sorted(callers):
                scope = nodes[caller].get('scope', {})
                candidates = workflows.get((scope.get('repository'), parsed.get('folder'),
                                             parsed.get('workflow_name')), []) if parsed else []
                rows.append({'caller_workflow_id': caller, 'command_owner_id': node['id'],
                    'property_ordinal': ordinal, 'evidence': prop.get('evidence', {}),
                    **parsed, 'candidate_workflow_ids': sorted(candidates),
                    'resolution': ('ambiguous' if len(candidates) > 1 else 'scoped_literal_candidate'
                                   if candidates else 'unresolved_or_unsupported'),
                    'resolution_reason': ('multiple_exact_export_candidates' if len(candidates) > 1 else
                        'exact_export_candidate_runtime_binding_unverified' if candidates else
                        'literal_child_missing_from_export' if parsed else
                        'dynamic_parameter_reference' if '$' in command else 'unsupported_command_syntax'),
                    'repository_binding': 'caller_export_repository_candidate_not_runtime_service_verification',
                    'relation': 'command_invocation_evidence_not_control_link',
                    'control_edge_created': False, 'runtime_verified': False,
                    'evidence_ids': sorted({caller, node['id'], *candidates})})
    return sorted(rows, key=lambda row: (row['caller_workflow_id'], row['command_owner_id'], row['property_ordinal']))


def _pmcmd(command):
    # Shell scripts, substitutions and compound commands require separate review.
    command = command.strip().removesuffix(';')
    if re.search(r'[;&|<>`\r\n()]', command):
        return {}
    try:
        tokens = shlex.split(command)
    except ValueError:
        return {}
    if tokens[:2] != ['pmcmd', 'startworkflow']:
        return {}
    values, wait_modes, positional = {}, [], []
    i = 2
    while i < len(tokens):
        token = tokens[i]
        if token in {'-wait', '-nowait'}:
            wait_modes.append(token)
            i += 1
        elif token in {'-sv', '-s', '-d', '-uv', '-pv', '-u', '-p', '-f'} and i + 1 < len(tokens):
            if token in values:
                return {}
            values[token] = tokens[i + 1]
            i += 2
        elif token.startswith('-'):
            return {}
        else:
            positional.append(token)
            i += 1
    if (len(positional) != 1 or len(wait_modes) > 1 or not _IDENTIFIER.fullmatch(positional[0])
            or not _IDENTIFIER.fullmatch(values.get('-f', ''))):
        return {}
    # Never return authentication flag values or complete command text.
    return {'workflow_name': positional[0], 'folder': values['-f'],
            'wait_mode': wait_modes[0][1:] if wait_modes else 'unspecified'}