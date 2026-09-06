"""Knowledge applicability and monotonic revision withdrawal (forward-only)."""
from alembic import op

revision = "20260906_0036"
down_revision = "20260905_0035"
branch_labels = None
depends_on = None
subject_linked_write = False
data_location_ids = ()


def upgrade():
    op.execute("""
        ALTER TABLE retrieval.knowledge_source_revisions
          DROP CONSTRAINT knowledge_source_revisions_schema_version_check,
          ADD CONSTRAINT knowledge_source_revisions_schema_version_check
          CHECK(schema_version IN ('knowledge-source-v0','knowledge-source-v1','knowledge-source-v2'))
    """)
    op.execute("""
        ALTER TABLE retrieval.knowledge_source_revisions
          ADD COLUMN channel TEXT NOT NULL DEFAULT 'global' CHECK(length(btrim(channel))>0),
          ADD COLUMN withdrawn_at TIMESTAMPTZ,
          ADD COLUMN withdrawal_reason TEXT,
          ADD CONSTRAINT knowledge_withdrawal_complete CHECK (
            (withdrawn_at IS NULL AND withdrawal_reason IS NULL) OR
            (withdrawn_at IS NOT NULL AND length(btrim(withdrawal_reason))>0)
          )
    """)
    op.execute("""
        ALTER TABLE retrieval.knowledge_source_manifests
          DROP CONSTRAINT knowledge_source_manifests_schema_version_check,
          ADD CONSTRAINT knowledge_source_manifests_schema_version_check
          CHECK(schema_version IN ('knowledge-source-v0','knowledge-source-temporal-v2'))
    """)
    op.execute("""
        CREATE FUNCTION retrieval.guard_knowledge_revision_lifecycle() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          IF (to_jsonb(OLD) - ARRAY['withdrawn_at','withdrawal_reason']) IS DISTINCT FROM
             (to_jsonb(NEW) - ARRAY['withdrawn_at','withdrawal_reason'])
             OR (OLD.withdrawn_at IS NOT NULL AND
                 (OLD.withdrawn_at,OLD.withdrawal_reason) IS DISTINCT FROM
                 (NEW.withdrawn_at,NEW.withdrawal_reason)) THEN
            RAISE EXCEPTION 'knowledge revision is immutable; withdrawal is monotonic' USING ERRCODE='55000';
          END IF;
          RETURN NEW;
        END $$
    """)
    op.execute("DROP TRIGGER knowledge_source_revisions_immutable ON retrieval.knowledge_source_revisions")
    op.execute("""
        CREATE TRIGGER knowledge_source_revisions_immutable BEFORE UPDATE
        ON retrieval.knowledge_source_revisions FOR EACH ROW
        EXECUTE FUNCTION retrieval.guard_knowledge_revision_lifecycle()
    """)


def downgrade():
    raise RuntimeError("knowledge applicability migration is forward-only")
