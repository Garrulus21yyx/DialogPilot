FROM pgvector/pgvector:0.8.6-pg18-bookworm

ARG PG_TEXTSEARCH_VERSION=1.4.0
ARG SCWS_VERSION=1.2.3
ARG ZHPARSER_VERSION=2.3

RUN test "$(dpkg --print-architecture)" = "amd64" \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       autoconf automake build-essential ca-certificates curl libtool \
       postgresql-server-dev-18 unzip \
    && curl -L --fail --retry 3 \
      "https://github.com/timescale/pg_textsearch/releases/download/v${PG_TEXTSEARCH_VERSION}/pg-textsearch-v${PG_TEXTSEARCH_VERSION}-pg18-amd64.zip" \
      -o /tmp/pg_textsearch.zip \
    && unzip /tmp/pg_textsearch.zip -d /tmp/pg_textsearch \
    && apt-get install -y /tmp/pg_textsearch/*.deb \
    && curl -L --fail --retry 3 \
      "https://github.com/hightman/scws/archive/refs/tags/${SCWS_VERSION}.tar.gz" \
      -o /tmp/scws.tar.gz \
    && mkdir /tmp/scws \
    && tar -xzf /tmp/scws.tar.gz --strip-components=1 -C /tmp/scws \
    && cd /tmp/scws \
    && touch README \
    && autoreconf -fi \
    && ./configure --with-pic \
    && make -j"$(nproc)" \
    && make install \
    && ldconfig \
    && curl -L --fail --retry 3 \
      "https://github.com/amutu/zhparser/archive/refs/tags/v${ZHPARSER_VERSION}.tar.gz" \
      -o /tmp/zhparser.tar.gz \
    && mkdir /tmp/zhparser \
    && tar -xzf /tmp/zhparser.tar.gz --strip-components=1 -C /tmp/zhparser \
    && make -C /tmp/zhparser -j"$(nproc)" PG_CONFIG=/usr/lib/postgresql/18/bin/pg_config \
    && make -C /tmp/zhparser install PG_CONFIG=/usr/lib/postgresql/18/bin/pg_config \
    && apt-get purge -y --auto-remove \
       autoconf automake build-essential curl libtool postgresql-server-dev-18 unzip \
    && rm -rf /var/lib/apt/lists/* /tmp/pg_textsearch /tmp/pg_textsearch.zip \
       /tmp/scws /tmp/scws.tar.gz /tmp/zhparser /tmp/zhparser.tar.gz

CMD ["postgres", "-c", "shared_preload_libraries=pg_textsearch"]
