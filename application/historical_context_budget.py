"""Budget historical source bodies without rewriting their provenance or state."""
import copy
import json
from dataclasses import replace

from application.context_budget import ContextBudgetManager, ModelContextBudgetExceeded


def fit_historical_payload(budget, payload, *, observation_path, trim_oldest_paths=(), overhead_tokens=0):
    """Externalize largest source bodies only when required by the call budget.

    Originals already belong to private Publication storage. References use the
    existing native reader; they are not summaries or substituted business facts.
    Mandatory provenance, coverage and receipt effects are never trimmed here.
    """
    if overhead_tokens:
        if overhead_tokens >= budget.available_tokens:
            raise ModelContextBudgetExceeded(overhead_tokens, budget.available_tokens)
        budget = ContextBudgetManager(context_window_tokens=budget.available_tokens - overhead_tokens,
                                      reserved_output_tokens=0, protocol_reserve_tokens=0)
    value = copy.deepcopy(dict(payload))
    original_tokens = budget._estimate(value)
    current = value
    for key in observation_path:
        current = current.get(key, {}) if isinstance(current, dict) else {}
    candidates = []
    for entry in current if isinstance(current, (list, tuple)) else ():
        if (entry.get('status') != 'HISTORICAL' or not entry.get('publication_id')
                or not entry.get('observation_id')):
            continue
        observation = entry.get('observation', {})
        bodies = [(fact, 'value_json', f'/facts/{index}/value')
                  for index, fact in enumerate(observation.get('facts', ()))]
        bodies += [(receipt['action'], 'arguments', f'/receipts/{index}/action/arguments')
                   for index, receipt in enumerate(observation.get('receipts', ()))
                   if isinstance(receipt.get('action'), dict)]
        bodies += [(recovery, 'detail', f'/write_recovery/{index}/detail')
                   for index, recovery in enumerate(observation.get('write_recovery', ()))]
        for owner, key, pointer in bodies:
            if key not in owner or owner[key] is None:
                continue
            reference = {'status': 'NOT_EXPANDED',
                'tool': 'read_conversation_observation', 'arguments': {
                    'publication_id': entry['publication_id'],
                    'observation_id': entry['observation_id'], 'pointer': pointer}}
            replacement_key = 'value_reference' if key == 'value_json' else key + '_reference'
            savings = budget._estimate({key: owner[key]}) - budget._estimate({replacement_key: reference})
            if savings > 0:
                candidates.append((savings, owner, key, replacement_key, reference))
    candidates.sort(key=lambda row: row[0], reverse=True)
    externalized = []
    while True:
        try:
            result = budget.fit_payload(value)
            break
        except ModelContextBudgetExceeded:
            if not candidates:
                result = budget.fit_payload(value, trim_oldest_paths=trim_oldest_paths)
                break
            _, owner, key, replacement_key, reference = candidates.pop(0)
            del owner[key]
            owner[replacement_key] = reference
            externalized.append(json.dumps(reference['arguments'], sort_keys=True))
    return replace(result, report=replace(result.report, original_tokens=original_tokens,
                                          externalized_items=tuple(externalized)))
