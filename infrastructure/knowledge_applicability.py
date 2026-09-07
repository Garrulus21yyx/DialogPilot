"""One SQL predicate for source applicability at retrieval and evidence reread."""
from __future__ import annotations
from psycopg import sql


def source_applicability(alias: str, *, as_of, as_of_end=None, region=None, channel=None, product=None):
    """Select the latest effective revision before filtering withdrawal/expiry.

    A withdrawn or expired newest revision never resurrects a superseded policy.
    Unknown business facets retain all scopes; known facets include global sources.
    Collection identity remains on chunks, separate from source product applicability.
    """
    c = sql.Identifier(alias)
    predicate = sql.SQL('''EXISTS (
        SELECT 1 FROM retrieval.knowledge_source_revisions applicability
        WHERE applicability.tenant_id={c}.tenant_id
          AND applicability.source_id={c}.source_id
          AND applicability.revision_id={c}.source_revision
          AND applicability.withdrawn_at IS NULL
          AND applicability.effective_from {start_op} %s
          AND (applicability.effective_to IS NULL OR applicability.effective_to > %s)
          AND NOT EXISTS (
            SELECT 1 FROM retrieval.knowledge_source_manifest_entries member
            JOIN retrieval.knowledge_source_revisions newer
              ON newer.tenant_id=member.tenant_id AND newer.source_id=member.source_id
             AND newer.revision_id=member.revision_id
            WHERE member.tenant_id={c}.tenant_id AND member.backend_id={c}.backend_id
              AND member.generation_id={c}.generation_id AND member.scope={c}.scope
              AND member.locale={c}.locale AND member.product=COALESCE({c}.product,'')
              AND newer.source_id=applicability.source_id
              AND newer.effective_from > applicability.effective_from
              AND newer.effective_from <= %s
          )''').format(c=c, start_op=sql.SQL('<' if as_of_end else '<='))
    params = [as_of_end or as_of, as_of, as_of]
    for name, value, general in (('region', region, 'global'), ('channel', channel, 'global'), ('product', product, '')):
        if value:
            predicate += sql.SQL(' AND applicability.{} IN (%s,%s)').format(sql.Identifier(name))
            params.extend((general, value))
    return predicate + sql.SQL(')'), params
