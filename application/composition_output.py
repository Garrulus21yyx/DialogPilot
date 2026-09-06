"""Application-owned segment attribution contract for conversation composition."""
from __future__ import annotations

import re


def composition_schema(claims):
    """Expose the request's finite attribution vocabulary; renderer checks linkage."""
    claim_ids = [claim['claim_id'] for claim in claims]
    if (not claim_ids or any(not isinstance(cid, str) or not cid.strip() for cid in claim_ids)
            or len(claim_ids) != len(set(claim_ids))):
        raise ValueError('composition requires unique claim identities')
    evidence_ids = sorted({item['evidence_id'] for claim in claims
                           if claim['kind'] == 'KNOWLEDGE_FACT'
                           for item in claim['value']['evidence']})
    if any(not isinstance(eid, str) or not eid.strip() for eid in evidence_ids):
        raise ValueError('invalid evidence identities')
    claim_choices = {"type": "array", "uniqueItems": True, "minItems": 1,
                     "items": {"type": "string", "enum": claim_ids}}
    evidence_choices = ({"type": "array", "uniqueItems": True,
                         "items": {"type": "string", "enum": evidence_ids}} if evidence_ids else
                        {"type": "array", "maxItems": 0, "items": {"type": "string"}})
    return {"type": "object", "additionalProperties": False, "required": ["segments"],
            "properties": {"segments": {"type": "array", "minItems": 1, "maxItems": 20,
                "items": {"type": "object", "additionalProperties": False,
                    "required": ["text", "claim_ids", "evidence_ids"], "properties": {
                        "text": {"type": "string", "minLength": 1},
                        "claim_ids": claim_choices, "evidence_ids": evidence_choices}}}}}


def validate_composition(value):
    if not isinstance(value, dict) or set(value) != {"segments"}:
        raise ValueError("composition requires segments")
    segments = value['segments']
    if not isinstance(segments, list) or not 1 <= len(segments) <= 20:
        raise ValueError("composition requires bounded segments")
    for segment in segments:
        if not isinstance(segment, dict) or set(segment) != {'text', 'claim_ids', 'evidence_ids'}:
            raise ValueError("invalid composition segment fields")
        if not isinstance(segment['text'], str) or not segment['text'].strip():
            raise ValueError("composition segment requires text")
        for key in ('claim_ids', 'evidence_ids'):
            ids = segment[key]
            if (not isinstance(ids, list) or any(not isinstance(i, str) or not i.strip() for i in ids)
                    or len(ids) != len(set(ids)) or (key == 'claim_ids' and not ids)):
                raise ValueError("invalid segment attribution")
        if re.search(r'\[(?:E[a-zA-Z0-9]+|(?:fact|outcome|receipt):[^\]]*)\]', segment['text']):
            raise ValueError("citations belong in structured attribution")
    return value


def render_composition(value, claims):
    """Check attribution membership per segment; semantic support is checked later."""
    value = validate_composition(value)
    by_id = {claim.claim_id: claim for claim in claims}
    texts, used = [], []
    for segment in value['segments']:
        selected = segment['claim_ids']
        if set(selected) - by_id.keys():
            raise ValueError("unknown segment claim")
        evidence_sets = [
            {item['evidence_id'] for item in by_id[cid].value['evidence']}
            for cid in selected if by_id[cid].kind == 'KNOWLEDGE_FACT'
        ]
        allowed = set().union(*evidence_sets)
        cited = set(segment['evidence_ids'])
        if cited - allowed or any(not cited.intersection(ids) for ids in evidence_sets):
            raise ValueError("segment evidence must belong to its knowledge claims")
        if any(cid in segment['text'] for cid in by_id):
            raise ValueError("internal claim ID in segment text")
        texts.append(segment['text'].strip() + (
            ' ' + ' '.join('[' + eid + ']' for eid in segment['evidence_ids']) if cited else ''))
        used.extend(cid for cid in selected if cid not in used)
    return '\n'.join(texts), tuple(used)
