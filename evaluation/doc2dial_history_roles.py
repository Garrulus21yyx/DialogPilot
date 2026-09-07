"""Recover historical benchmark roles from the authoritative archive, never parity."""
import json
import zipfile


def history_roles(archive, cases, member='doc2dial_dial_validation.json'):
    with zipfile.ZipFile(archive) as z:
        data = json.loads(z.read(member))['dial_data']
    dialogues = {d['dial_id']: d['turns'] for domain in data.values() for values in domain.values() for d in values}
    result = {}
    for case in cases:
        dialogue_id = case.group_id.removeprefix('doc2dial-')
        turn_id = int(case.case_id.rsplit('-', 1)[1])
        turns = dialogues[dialogue_id]
        index = next(i for i,t in enumerate(turns) if t['turn_id'] == turn_id)
        assert turns[index]['utterance'] == case.query
        assert tuple(t['utterance'] for t in turns[:index]) == case.history
        mapping = {'user': 'user', 'agent': 'assistant'}
        result[case.case_id] = tuple(mapping[t['role']] for t in turns[:index])
    return result
