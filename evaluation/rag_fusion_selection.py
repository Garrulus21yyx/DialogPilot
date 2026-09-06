"""Grouped out-of-fold comparison; labels select weights on training folds only."""

import re
from evaluation.rag_provider_free import WEIGHTS, sha


def query_class(query):
    if re.search(r"\b[A-Za-z]+[-_]?\d+[A-Za-z0-9-]*\b", query):
        return "identifier"
    if "[CONVERSATION]" in query:
        return "conversation"
    return "standalone"


def evaluate_selection(rows, folds=5):
    if folds < 2:
        raise ValueError("at least two folds required")
    grouped = {}
    for row in rows:
        key = row["case_id"]
        if row["dense_weight"] in grouped.setdefault(key, {}):
            raise ValueError("duplicate case weight")
        grouped[key][row["dense_weight"]] = row
    if any(set(v) != set(WEIGHTS) for v in grouped.values()):
        raise ValueError("incomplete weight captures")
    cases = [values[0.25] for values in grouped.values()]
    groups = sorted({c["group_id"] for c in cases}, key=sha)
    if len(groups) < folds:
        raise ValueError("too few groups")
    fold_by_group = {g: i % folds for i, g in enumerate(groups)}
    output = []

    def choose(training):
        if not training:
            return 0.25
        return max(
            WEIGHTS,
            key=lambda w: (
                sum(
                    grouped[c["case_id"]][w]["metrics"]["tool_message_complete"]
                    for c in training
                ),
                w == 0.25,
                -w,
            ),
        )

    for case in cases:
        fold = fold_by_group[case["group_id"]]
        training = [c for c in cases if fold_by_group[c["group_id"]] != fold]
        global_weight = choose(training)
        scoped = [c for c in training if c["corpus_type"] == case["corpus_type"]]
        classified = [
            c for c in scoped if query_class(c["query"]) == query_class(case["query"])
        ]
        # Small buckets use the simpler parent estimate; threshold is frozen.
        weights = {
            "fixed_oof": global_weight,
            "corpus_oof": choose(scoped) if len(scoped) >= 10 else global_weight,
            "query_oof": choose(classified)
            if len(classified) >= 10
            else (choose(scoped) if len(scoped) >= 10 else global_weight),
        }
        output.append(
            {
                "case_id": case["case_id"],
                "group_id": case["group_id"],
                "fold": fold,
                "corpus_type": case["corpus_type"],
                "query_class": query_class(case["query"]),
                "weights": weights,
                "success": {
                    name: grouped[case["case_id"]][w]["metrics"][
                        "tool_message_complete"
                    ]
                    for name, w in weights.items()
                },
            }
        )
    summaries = []
    for corpus in ("all", "public_doc2dial", "synthetic_ecommerce"):
        subset = [r for r in output if corpus == "all" or r["corpus_type"] == corpus]
        if not subset:
            continue
        for strategy in ("fixed_oof", "corpus_oof", "query_oof"):
            rescued = [
                r["case_id"]
                for r in subset
                if r["success"][strategy] and not r["success"]["fixed_oof"]
            ]
            harmed = [
                r["case_id"]
                for r in subset
                if not r["success"][strategy] and r["success"]["fixed_oof"]
            ]
            summaries.append(
                {
                    "corpus_type": corpus,
                    "strategy": strategy,
                    "n": len(subset),
                    "complete": sum(r["success"][strategy] for r in subset),
                    "rescued": rescued,
                    "harmed": harmed,
                    "delta_pp": 100 * (len(rescued) - len(harmed)) / len(subset),
                }
            )
    return {
        "scope": "development grouped cross-validation, not heldout acceptance",
        "folds": folds,
        "groups": len(groups),
        "metrics": summaries,
        "cases": output,
    }
