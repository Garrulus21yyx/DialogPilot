"""Move quality observations and intent feedback audit to PostgreSQL."""
from alembic import op

revision = "20260905_0034"
down_revision = "20260905_0033"
branch_labels = None
depends_on = None
subject_linked_write = False
data_location_ids = ()


def upgrade() -> None:
    op.execute("""
        CREATE TABLE dialogpilot_platform.bad_cases (
            badcase_id TEXT PRIMARY KEY,
            fingerprint TEXT NOT NULL UNIQUE,
            semantic_group_id TEXT NOT NULL,
            source TEXT NOT NULL,
            stage TEXT NOT NULL,
            severity TEXT NOT NULL,
            status TEXT NOT NULL,
            symptom_code TEXT NOT NULL,
            trace_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            user_ref TEXT NOT NULL,
            sanitized_input TEXT NOT NULL,
            published_response TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            versions_json TEXT NOT NULL,
            eval_layer TEXT NOT NULL,
            expected_json TEXT NOT NULL,
            reproduction_json TEXT NOT NULL,
            root_cause TEXT NOT NULL,
            owner_module TEXT NOT NULL,
            linked_case_id TEXT NOT NULL,
            fixed_by_commit TEXT NOT NULL,
            occurrence_count INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            prediction_id TEXT NOT NULL DEFAULT '',
            predicted_intent TEXT NOT NULL DEFAULT '',
            suggested_intent TEXT NOT NULL DEFAULT '',
            approved_intent TEXT NOT NULL DEFAULT '',
            annotation_id TEXT NOT NULL DEFAULT '',
            classifier_fingerprint TEXT NOT NULL DEFAULT ''
        )
    """)
    op.execute("""
        CREATE INDEX idx_bad_cases_queue
        ON dialogpilot_platform.bad_cases(status, severity, last_seen_at)
    """)
    op.execute("""
        CREATE TABLE dialogpilot_platform.bad_case_events (
            event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            badcase_id TEXT NOT NULL,
            from_status TEXT,
            to_status TEXT NOT NULL,
            actor TEXT NOT NULL,
            note TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(badcase_id) REFERENCES dialogpilot_platform.bad_cases(badcase_id)
        )
    """)
    op.execute("""
        CREATE TABLE dialogpilot_platform.bad_case_occurrences (
            occurrence_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            badcase_id TEXT NOT NULL,
            source TEXT NOT NULL,
            severity TEXT NOT NULL,
            trace_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            sanitized_input TEXT NOT NULL,
            published_response TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            versions_json TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            FOREIGN KEY(badcase_id) REFERENCES dialogpilot_platform.bad_cases(badcase_id)
        )
    """)
    op.execute("""
        CREATE INDEX idx_bad_case_occurrences
        ON dialogpilot_platform.bad_case_occurrences(badcase_id, occurrence_id)
    """)
    op.execute("""
        CREATE TABLE dialogpilot_platform.intent_learning_records (
            prediction_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL,
            trace_id TEXT NOT NULL,
            conv_id TEXT NOT NULL,
            user_ref TEXT NOT NULL,
            input_fingerprint TEXT NOT NULL,
            sanitized_input TEXT NOT NULL,
            predicted_intent TEXT NOT NULL,
            confidence DOUBLE PRECISION NOT NULL,
            source_scores_json TEXT NOT NULL,
            classifier_fingerprint TEXT NOT NULL,
            bundle_version TEXT NOT NULL,
            feedback_status TEXT NOT NULL,
            suggested_intent TEXT NOT NULL,
            feedback_reason TEXT NOT NULL,
            badcase_id TEXT NOT NULL,
            approved_intent TEXT NOT NULL,
            annotation_id TEXT NOT NULL,
            reviewer TEXT NOT NULL,
            dataset_version TEXT NOT NULL,
            review_note TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    op.execute("""
        CREATE INDEX idx_intent_learning_queue
        ON dialogpilot_platform.intent_learning_records(feedback_status, updated_at)
    """)
    op.execute("""
        CREATE INDEX idx_intent_learning_request
        ON dialogpilot_platform.intent_learning_records(request_id, created_at)
    """)
    op.execute("""
        CREATE TABLE dialogpilot_platform.intent_learning_events (
            event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            prediction_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            actor TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(prediction_id) REFERENCES dialogpilot_platform.intent_learning_records(prediction_id)
        )
    """)
    op.execute("""
        CREATE INDEX idx_intent_learning_events
        ON dialogpilot_platform.intent_learning_events(prediction_id, event_id)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE dialogpilot_platform.intent_learning_events")
    op.execute("DROP TABLE dialogpilot_platform.intent_learning_records")
    op.execute("DROP TABLE dialogpilot_platform.bad_case_occurrences")
    op.execute("DROP TABLE dialogpilot_platform.bad_case_events")
    op.execute("DROP TABLE dialogpilot_platform.bad_cases")
