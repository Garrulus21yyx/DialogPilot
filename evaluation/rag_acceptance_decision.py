"""Paired local evidence acceptance with predeclared, independently checked gates."""

import math


def exact_paired_p(rescued, harmed):
    if min(rescued, harmed) < 0:
        raise ValueError("counts must be nonnegative")
    n = rescued + harmed
    return min(
        1.0, 2 * sum(math.comb(n, k) for k in range(min(rescued, harmed) + 1)) / 2**n
    )


def decide(dataset, before, after, contract):
    old = {r["case_id"]: r for r in before}
    new = {r["case_id"]: r for r in after}
    cases = {c.case_id: c for c in dataset.cases}
    docs = {d.document_id: d for d in dataset.documents}
    if (
        len(old) != len(before)
        or len(new) != len(after)
        or set(old) != set(new)
        or set(old) != set(cases)
    ):
        raise ValueError("acceptance requires unique, identical complete case sets")
    output = []
    for cid, case in cases.items():
        pair = [old[cid], new[cid]]
        if pair[0]["query"] != pair[1]["query"] or pair[0]["fused"] != pair[1]["fused"]:
            raise ValueError("candidate/query drift")
        measured = []
        for row in pair:
            evidence = row["tool_message"].get("evidence", [])
            for item in evidence:
                ref = item["source"]
                doc = docs[ref["source_id"]]
                start, end = ref["start_char"], ref["end_char"]
                if (
                    not 0 <= start < end <= len(doc.content)
                    or item["text"] != doc.content[start:end]
                ):
                    raise ValueError("source provenance drift")
            complete = all(
                any(
                    e["source"]["source_id"] == span.document_id
                    and e["source"]["start_char"] <= span.start_char
                    and e["source"]["end_char"] >= span.end_char
                    for e in evidence
                )
                for span in case.evidence
            )
            if complete != row["metrics"]["tool_message_complete"]:
                raise ValueError("reported metric differs from source spans")
            measured.append(complete)
        output.append(
            {
                "case_id": cid,
                "group_id": case.group_id,
                "before": measured[0],
                "after": measured[1],
            }
        )
    rescued = [r["case_id"] for r in output if r["after"] and not r["before"]]
    harmed = [r["case_id"] for r in output if r["before"] and not r["after"]]
    p = exact_paired_p(len(rescued), len(harmed))
    quality_pass = (
        len(rescued) > len(harmed)
        and p
        < contract["success_criteria"]["paired_exact_mcnemar_two_sided_p_less_than"]
    )
    return {
        "decision": "EVIDENCE_QUALITY_PASS"
        if quality_pass
        else "EVIDENCE_QUALITY_NOT_DEMONSTRATED",
        "scope": contract["decision_scope"],
        "n": len(cases),
        "baseline_complete": sum(r["before"] for r in output),
        "reranked_complete": sum(r["after"] for r in output),
        "rescued": rescued,
        "harmed": harmed,
        "net_pp": 100 * (len(rescued) - len(harmed)) / len(cases),
        "exact_paired_two_sided_p": p,
        "candidate_sets_verified_equal": True,
        "source_provenance_verified": True,
        "cases": output,
    }
