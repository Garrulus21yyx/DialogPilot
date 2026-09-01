#!/usr/bin/env python3
"""Fail-closed linter for a frozen/decided gate archive."""
from __future__ import annotations

import argparse
import json

from evaluation.gates import (
    load_decision,
    load_evidence,
    load_manifest,
    validate_gate_archive,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--decision", required=True)
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    evidence = load_evidence(args.evidence)
    decision = load_decision(args.decision)
    validate_gate_archive(manifest, evidence, decision)
    print(json.dumps({
        "gate_id": manifest.spec.gate_id,
        "version": manifest.spec.version,
        "state": manifest.state.value,
        "decision": decision.status.value,
        "manifest_sha256": manifest.manifest_sha256,
        "evidence_sha256": evidence.evidence_sha256,
        "decision_sha256": decision.decision_sha256,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
