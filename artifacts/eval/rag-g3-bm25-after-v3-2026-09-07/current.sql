
            WITH query_terms AS (
                SELECT token FROM unnest(%s::text[]) AS token
            ),
            scoped AS MATERIALIZED (
                SELECT candidate_id, "source_id", "source_revision",
                       provenance_sha256, "projected_at", lexical_terms,
                       cardinality(lexical_terms)::double precision AS dl
                FROM retrieval."knowledge_chunk_search"
                WHERE tenant_id=%s AND generation_id=%s AND scope=%s AND locale=%s AND EXISTS (
        SELECT 1 FROM retrieval.knowledge_source_revisions applicability
        WHERE applicability.tenant_id="knowledge_chunk_search".tenant_id
          AND applicability.source_id="knowledge_chunk_search".source_id
          AND applicability.revision_id="knowledge_chunk_search".source_revision
          AND applicability.withdrawn_at IS NULL
          AND applicability.effective_from <= %s
          AND (applicability.effective_to IS NULL OR applicability.effective_to > %s)
          AND NOT EXISTS (
            SELECT 1 FROM retrieval.knowledge_source_manifest_entries member
            JOIN retrieval.knowledge_source_revisions newer
              ON newer.tenant_id=member.tenant_id AND newer.source_id=member.source_id
             AND newer.revision_id=member.revision_id
            WHERE member.tenant_id="knowledge_chunk_search".tenant_id AND member.backend_id="knowledge_chunk_search".backend_id
              AND member.generation_id="knowledge_chunk_search".generation_id AND member.scope="knowledge_chunk_search".scope
              AND member.locale="knowledge_chunk_search".locale AND member.product=COALESCE("knowledge_chunk_search".product,'')
              AND newer.source_id=applicability.source_id
              AND newer.effective_from > applicability.effective_from
              AND newer.effective_from <= %s
          ))
            ),
            -- Reuse scope statistics once, independently of join-plan selection.
            stats AS MATERIALIZED (
                SELECT count(*)::double precision AS n,
                       GREATEST(avg(dl), 1.0) AS avgdl
                FROM scoped
            ),
            term_frequency AS MATERIALIZED (
                SELECT d.candidate_id, d.dl, terms.value AS token,
                       count(*)::double precision AS tf
                FROM scoped d
                CROSS JOIN LATERAL unnest(d.lexical_terms) AS terms(value)
                JOIN query_terms q ON q.token = terms.value
                GROUP BY d.candidate_id, d.dl, terms.value
            ),
            term_stats AS MATERIALIZED (
                SELECT token, count(*)::double precision AS df
                FROM term_frequency
                GROUP BY token
            ),
            scores AS (
                SELECT tf.candidate_id,
                       sum(
                           ln(1.0 + (stats.n - ts.df + 0.5) / (ts.df + 0.5))
                           * (tf.tf * 2.2)
                           / (tf.tf + 1.2 * (0.25 + 0.75 * tf.dl / stats.avgdl))
                           ORDER BY tf.token
                       ) AS score
                FROM term_frequency tf
                JOIN term_stats ts USING (token)
                CROSS JOIN stats
                GROUP BY tf.candidate_id
            )
            SELECT tf.candidate_id, d."source_id", d."source_revision",
                   d.provenance_sha256, tf.score, d."projected_at"
            FROM scores tf
            JOIN scoped d USING (candidate_id)
            ORDER BY score DESC, tf.candidate_id
            LIMIT %s
