import itertools
from scripts.lock_rag_external_datasets import split_conversations,DOMAINS


def tasks():
    return [{'conversation_id':f'{d}-{i}','task_id':f'{d}-{i}<::>{t}',
             'Collection':f'mt-rag-{"ibmcloud" if d=="cloud" else d}-elser'}
            for d in DOMAINS for i in range(10) for t in range(1,4)]


def test_group_split_is_input_order_invariant_and_never_splits_turns():
    source=tasks();a=split_conversations(source);assert a==split_conversations(list(reversed(source)))
    for d in DOMAINS:
        for split,n in [('dev',7),('heldout',3)]:
            group=[r for r in a if r['domain']==d and r['split']==split]
            assert len(group)==n
            assert all(len(r['task_ids'])==3 for r in group)
    flat=[t for r in a for t in r['task_ids']]
    assert len(flat)==len(set(flat))==len(source)


def test_duplicate_task_cannot_silently_enter_split():
    import pytest
    with pytest.raises(AssertionError):split_conversations(tasks()+tasks()[:1])
