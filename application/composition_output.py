"""Application-owned, content-bound support selection for answer composition."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import asdict


def support_catalog(claims):
    """Enumerate legal attribution edges; no model may manufacture their pairing."""
    rows = [asdict(c) if not isinstance(c, dict) else c for c in claims]
    ids = [c['claim_id'] for c in rows]
    if not ids or any(not isinstance(i, str) or not i.strip() for i in ids) or len(ids) != len(set(ids)):
        raise ValueError('composition requires unique claim identities')
    catalog = []
    for claim in sorted(rows, key=lambda c: c['claim_id']):
        if claim['kind'] == 'KNOWLEDGE_FACT':
            evidence = [e['evidence_id'] for e in claim['value']['evidence']]
            if any(not isinstance(e, str) or not e.strip() for e in evidence) or len(evidence) != len(set(evidence)):
                raise ValueError('knowledge claim requires unique evidence identities')
            evidence = sorted(evidence)
        elif claim['kind'] in {'FACT', 'RECEIPT', 'WORK_ITEM_OUTCOME'}:
            evidence = [None]
        else:
            raise ValueError('unsupported composition claim kind')
        for eid in evidence:
            identity = json.dumps({'claim': claim, 'evidence_id': eid}, ensure_ascii=False,
                                  sort_keys=True, separators=(',', ':'), allow_nan=False)
            catalog.append({'support_id': 'S' + hashlib.sha256(identity.encode()).hexdigest()[:20],
                            'claim_id': claim['claim_id'], 'evidence_id': eid})
    if not catalog or len({c['support_id'] for c in catalog}) != len(catalog):
        raise ValueError('composition requires distinct usable supports')
    return catalog


def prepare_composition_payload(payload):
    """Capture the exact map next to the immutable facts before model budgeting."""
    value = copy.deepcopy(dict(payload))
    catalog = support_catalog(value['allowed_claims'])
    if 'support_catalog' in value and value['support_catalog'] != catalog:
        raise ValueError('captured support catalog disagrees with claims')
    value['schema_version'] = 'conversation-compose-request-v3-supports'
    value['support_catalog'] = catalog
    return value


def composition_schema(claims):
    ids = [row['support_id'] for row in support_catalog(claims)]
    return {'type': 'object', 'additionalProperties': False, 'required': ['segments'],
            'properties': {'segments': {'type': 'array', 'minItems': 1, 'maxItems': 20,
                'items': {'type': 'object', 'additionalProperties': False,
                    'required': ['text', 'support_ids'], 'properties': {
                        'text': {'type': 'string', 'minLength': 1},
                        'support_ids': {'type': 'array', 'minItems': 1, 'uniqueItems': True,
                                        'items': {'type': 'string', 'enum': ids}}}}}}}


def validate_composition(value):
    if not isinstance(value, dict) or set(value) != {'segments'}:
        raise ValueError('composition requires segments')
    if not isinstance(value['segments'], list) or not 1 <= len(value['segments']) <= 20:
        raise ValueError('composition requires bounded segments')
    for segment in value['segments']:
        if not isinstance(segment, dict) or set(segment) != {'text', 'support_ids'}:
            raise ValueError('composition requires text and support_ids')
        if not isinstance(segment['text'], str) or not segment['text'].strip():
            raise ValueError('composition requires text')
        ids = segment['support_ids']
        if (not isinstance(ids, list) or not ids or any(not isinstance(i, str) or not i.strip() for i in ids)
                or len(ids) != len(set(ids))):
            raise ValueError('composition requires distinct support identities')
        if re.search(r'\[(?:E[a-zA-Z0-9]+|S[a-f0-9]+|(?:fact|outcome|receipt):[^\]]*)\]', segment['text']):
            raise ValueError('citations belong in support selection')
    return value


def render_composition(value, claims):
    value = validate_composition(value)
    catalog = support_catalog(claims)
    supports = {row['support_id']: row for row in catalog}
    internal_ids = set(supports) | {row['claim_id'] for row in catalog}
    texts, used = [], []
    for segment in value['segments']:
        if set(segment['support_ids']) - supports.keys():
            raise ValueError('unknown support identity')
        if any(identifier in segment['text'] for identifier in internal_ids):
            raise ValueError('internal attribution identity in response text')
        cited = []
        for sid in segment['support_ids']:
            support = supports[sid]
            if support['claim_id'] not in used:
                used.append(support['claim_id'])
            eid = support['evidence_id']
            if eid is not None and eid not in cited:
                cited.append(eid)
        texts.append(segment['text'].strip() + (' ' + ' '.join('[' + e + ']' for e in cited) if cited else ''))
    return '\n'.join(texts), tuple(used)
