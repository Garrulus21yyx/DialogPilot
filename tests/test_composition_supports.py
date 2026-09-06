import copy
from dataclasses import asdict, replace
from itertools import combinations

import pytest
from jsonschema import Draft202012Validator

from application.composition_output import composition_schema, support_catalog, prepare_composition_payload, render_composition
from application.response_assembly import AllowedClaim
from evaluation.legacy_composition_output import convert_valid_legacy_composition


def claims():
    return (
        AllowedClaim('k1', 'KNOWLEDGE_FACT', {'evidence': [{'evidence_id': 'E1'}, {'evidence_id': 'Eshared'}]}, ()),
        AllowedClaim('k2', 'KNOWLEDGE_FACT', {'evidence': [{'evidence_id': 'E2'}, {'evidence_id': 'Eshared'}]}, ()),
        AllowedClaim('empty', 'KNOWLEDGE_FACT', {'evidence': []}, ()),
        AllowedClaim('b', 'FACT', {'status': 'paid'}, ()),
        AllowedClaim('o', 'WORK_ITEM_OUTCOME', {'status': 'RETRYABLE_FAILURE'}, ()),
    )


def test_all_support_subsets_preserve_owned_relations():
    source = claims()
    catalog = support_catalog(source)
    assert all(row['claim_id'] != 'empty' for row in catalog)
    validator = Draft202012Validator(composition_schema([asdict(c) for c in source]))
    for count in range(1, len(catalog) + 1):
        for selected in combinations(catalog, count):
            value = {'segments': [{'text': '有条件的结论。', 'support_ids': [s['support_id'] for s in selected]}]}
            assert validator.is_valid(value)
            text, used = render_composition(value, source)
            assert set(used) == {s['claim_id'] for s in selected}
            for eid in ('E1', 'E2', 'Eshared'):
                assert text.count('[' + eid + ']') == int(any(s['evidence_id'] == eid for s in selected))


def test_support_map_is_deterministic_content_bound_and_captured():
    source = claims()
    assert support_catalog(source) == support_catalog(tuple(reversed(source)))
    payload = {'allowed_claims': [asdict(c) for c in source]}
    original = copy.deepcopy(payload)
    prepared = prepare_composition_payload(payload)
    assert payload == original and prepare_composition_payload(prepared) == prepared
    changed = tuple(replace(c, value={'status': 'refunded'}) if c.claim_id == 'b' else c for c in source)
    sid = next(s['support_id'] for s in prepared['support_catalog'] if s['claim_id'] == 'b')
    with pytest.raises(ValueError):
        render_composition({'segments': [{'text': '结论', 'support_ids': [sid]}]}, changed)
    prepared['support_catalog'][0]['claim_id'] = 'wrong'
    with pytest.raises(ValueError): prepare_composition_payload(prepared)


@pytest.mark.parametrize('segment', [
    {'text': '结论', 'claim_ids': ['b'], 'evidence_ids': ['E1']},
    {'text': '结论', 'support_ids': []},
    {'text': '结论', 'support_ids': ['unknown']},
    {'text': '结论 [E1]', 'support_ids': ['unknown']},
])
def test_legacy_or_invalid_support_selection_is_not_repaired(segment):
    with pytest.raises(ValueError): render_composition({'segments': [segment]}, claims())


def test_legacy_conversion_requires_original_correct_attribution():
    bad = {'segments': [{'text': '政策结论', 'claim_ids': ['b'], 'evidence_ids': ['E1']}]}
    with pytest.raises(ValueError): convert_valid_legacy_composition(bad, claims())
    good = {'segments': [{'text': '政策结论', 'claim_ids': ['k1', 'k2', 'b'], 'evidence_ids': ['Eshared']}]}
    migrated = convert_valid_legacy_composition(good, claims())
    text, used = render_composition(migrated, claims())
    assert text.count('[Eshared]') == 1 and set(used) == {'k1', 'k2', 'b'}


def test_duplicate_ids_internal_handles_and_empty_support_pool_reject():
    source = claims()
    sid = support_catalog(source)[0]['support_id']
    for segment in ({'text': '结论', 'support_ids': [sid, sid]},
                    {'text': '内部引用 ' + sid, 'support_ids': [sid]}):
        with pytest.raises(ValueError): render_composition({'segments': [segment]}, source)
    with pytest.raises(ValueError): support_catalog((source[2],))
