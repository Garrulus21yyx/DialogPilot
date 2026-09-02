"""PostgreSQL ownership proofs for versioned user memory facts."""
import json

from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
from infrastructure.postgres_memory_fact_store import PostgresMemoryFactStore


def _document(fact_id, *, user_id="user-1", conversation_id="conversation-1"):
    return {
        "fact_id": fact_id,
        "user_id": user_id,
        "key": "preferred_language",
        "value": "zh-CN",
        "status": "active",
        "confidence": 0.9,
        "source_message_ids": ["message-1"],
        "source_max_seq": 1,
        "observed_at": "2026-09-02T00:00:00+00:00",
        "updated_at": "2026-09-02T00:00:00+00:00",
        "source_conversation_id": conversation_id,
        "source_observed_at": "2026-09-02T00:00:00+00:00",
        "superseded_by": "",
    }


def _metadata(document):
    return {
        "user_id": document["user_id"],
        "fact_key": document["key"],
        "status": document["status"],
        "observed_at": document["observed_at"],
        "source_conversation_id": document["source_conversation_id"],
    }


def test_postgres_memory_facts_upsert_read_and_scoped_delete(postgres_database_url):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(
        postgres_database_url, min_size=1, max_size=2,
    ))
    pool.open()
    store = PostgresMemoryFactStore(pool)
    first = _document("fact-1")
    other_conversation = _document("fact-2", conversation_id="conversation-2")
    other_user = _document("fact-3", user_id="user-2")
    with pool.transaction() as connection:
        connection.execute("TRUNCATE dialogpilot_app.memory_facts")
    try:
        for document in (first, other_conversation, other_user):
            store.upsert(
                ids=[document["fact_id"]],
                documents=[json.dumps(document)],
                metadatas=[_metadata(document)],
            )
        loaded = [
            json.loads(item)
            for item in store.get(where={"user_id": "user-1"})["documents"]
        ]
        assert [item["fact_id"] for item in loaded] == ["fact-1", "fact-2"]

        store.delete(where={"$and": [
            {"user_id": {"$eq": "user-1"}},
            {"source_conversation_id": {"$eq": "conversation-1"}},
        ]})
        remaining = [
            json.loads(item)["fact_id"]
            for item in store.get(where={"user_id": "user-1"})["documents"]
        ]
        assert remaining == ["fact-2"]
        assert store.get(where={"user_id": "user-2"})["documents"]
    finally:
        pool.close()
