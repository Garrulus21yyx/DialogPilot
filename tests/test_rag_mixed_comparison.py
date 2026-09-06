import copy
import itertools
import pytest
from evaluation.rag_mixed_comparison import SIGNALS, compare


def summary(bits):
    return {'definitions_sha256': 'same', 'capture_sha256': 'capture',
            'complete_capture': True, 'expected_cases': len(bits),
            'captured_cases': len(bits), 'api_calls': 4 * len(bits),
            'results': [{'case_id': str(i), **dict.fromkeys(SIGNALS, b)}
                        for i, b in enumerate(bits)]}


def test_all_two_case_transitions_have_conserved_paired_counts():
    for before, after in itertools.product(itertools.product((False, True), repeat=2), repeat=2):
        result = compare(summary(before), summary(after))
        for metric in result['metrics'].values():
            assert metric['after'] - metric['before'] == len(metric['rescued']) - len(metric['harmed'])
            assert not set(metric['rescued']) & set(metric['harmed'])
            reverse = compare(summary(after), summary(before))['metrics'][SIGNALS[0]]
            assert metric['delta_pp'] == -reverse['delta_pp']


@pytest.mark.parametrize('mutation', [
    lambda s: s.update(definitions_sha256='different'),
    lambda s: s.update(complete_capture=False),
    lambda s: s.update(expected_cases=3),
    lambda s: s['results'][0].update(case_id='other'),
    lambda s: s['results'][1].update(case_id='0'),
    lambda s: s['results'][0].update(expected_source_visible=None),
])
def test_comparison_rejects_unpaired_or_incomplete_evidence(mutation):
    before = summary([False, True])
    after = copy.deepcopy(before)
    mutation(after)
    with pytest.raises(ValueError):
        compare(before, after)
