from scripts.freeze_wixqa_comparison import components

def test_shared_article_transitivity_closes_group_boundary():
    assert sorted(map(sorted,components([['a'],['a','b'],['b','c'],['z'],['z','y']])))==[[0,1,2],[3,4]]

def test_component_membership_is_invariant_to_article_order_and_duplicates():
    inputs=[['b','a','a'],['c','b'],['z']]
    assert sorted(map(sorted,components(inputs)))==sorted(map(sorted,components([sorted(set(x)) for x in inputs])))
