FROM pgvector/pgvector:0.8.6-pg18-bookworm

ARG PG_TEXTSEARCH_VERSION=1.4.0
RUN test "$(dpkg --print-architecture)" = "amd64" \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip \
    && curl -L --fail --retry 3 \
      "https://github.com/timescale/pg_textsearch/releases/download/v${PG_TEXTSEARCH_VERSION}/pg-textsearch-v${PG_TEXTSEARCH_VERSION}-pg18-amd64.zip" \
      -o /tmp/pg_textsearch.zip \
    && unzip /tmp/pg_textsearch.zip -d /tmp/pg_textsearch \
    && apt-get install -y /tmp/pg_textsearch/*.deb \
    && rm -rf /var/lib/apt/lists/* /tmp/pg_textsearch /tmp/pg_textsearch.zip

CMD ["postgres", "-c", "shared_preload_libraries=pg_textsearch"]
