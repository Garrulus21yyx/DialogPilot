"""Evaluation-only capture of the same search's pre-fusion pools.

No second embedding/search. Projection reads for unselected candidates add audit
cost; their timing is separated and must not be used as production latency.
"""
from copy import deepcopy
from threading import Lock, local
from time import perf_counter
from infrastructure.postgres_knowledge_retriever import PostgresKnowledgeCandidateSource
from application.knowledge_retriever import KnowledgeCandidateResult


class RecordedKnowledgeSource(PostgresKnowledgeCandidateSource):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.records = []
        self._record_lock = Lock()
        self._record_local = local()

    def _collect_sources(self, request, variants, **kwargs):
        collected = super()._collect_sources(request, variants, **kwargs)
        if not isinstance(collected, KnowledgeCandidateResult):
            self._record_local.collected = deepcopy(collected)
        return collected

    def _search(self, request, variants, top_k):
        self._record_local.collected = None
        started = perf_counter()
        record = {'query': request.query, 'variants': list(variants), 'top_k': top_k,
                  'request_scope': {key: getattr(request, key, None) for key in (
                      'tenant_id', 'generation_id', 'manifest_fingerprint', 'as_of', 'as_of_end',
                      'applicable_region', 'applicable_channel', 'applicable_product')},
                  'rrf_k': request.policy.rrf_k}
        try:
            result = super()._search(request, variants, top_k)
            record.update(status=result.status.value, detail_code=result.detail_code,
                          fused_candidates=deepcopy(list(result.candidates)),
                          search_ms=(perf_counter()-started)*1000)
            collected = self._record_local.collected
            if collected is not None:
                before = perf_counter()
                record.update(route_ranks=collected.ranks, route_weights=collected.route_weights,
                              authority=collected.authority)
                union = self._load_rows(request, collected.generation, sorted(collected.authority))
                record.update(source_union=deepcopy(union), projection_ms=(perf_counter()-before)*1000,
                              projection_complete=set(union)==set(collected.authority))
                record['selected_projection_matches'] = all(
                    c['chunk_id'] in union and all(c.get(k)==v for k,v in union[c['chunk_id']].items())
                    for c in result.candidates)
            return result
        except Exception as exc:
            record['capture_error'] = type(exc).__name__
            raise
        finally:
            with self._record_lock:
                self.records.append(record)
            self._record_local.collected = None
