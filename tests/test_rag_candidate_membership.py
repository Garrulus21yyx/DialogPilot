"""Budget and dedup properties of the experimental candidate selector."""
import random
from scripts.replay_rag_candidate_membership import balanced_membership


def test_generated_membership_conserves_unique_budget_and_handles_exhaustion():
    rng = random.Random(731)
    for _ in range(100):
        routes = [rng.choices(range(40), k=rng.randrange(35)) for _ in range(rng.randrange(1, 5))]
        universe = set().union(*map(set, routes))
        for budget in (0, 1, 5, 20, 80):
            actual = balanced_membership(routes, budget)
            assert len(actual) == len(set(actual)) == min(budget, len(universe))
            assert set(actual) <= universe
            assert actual == balanced_membership(routes, budget)


def test_disjoint_two_routes_share_slots_with_reallocation():
    assert balanced_membership([[1, 2, 3], [4, 5, 6]], 4) == [1, 4, 2, 5]
    assert balanced_membership([[1], [4, 5, 6]], 4) == [1, 4, 5, 6]
    assert balanced_membership([], 20) == []
