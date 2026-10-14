"""Framework-owned LangGraph checkpoints in an isolated PostgreSQL schema.

Frozen from langgraph-checkpoint-postgres 3.1.2; future upgrades require an explicit migration.
"""
from alembic import op
revision='0007_langgraph_checkpoints'
down_revision='0006_processing_ledger'
branch_labels=None
depends_on=None
STATEMENTS=['CREATE TABLE IF NOT EXISTS checkpoint_migrations (\n    v INTEGER PRIMARY KEY\n);', "CREATE TABLE IF NOT EXISTS checkpoints (\n    thread_id TEXT NOT NULL,\n    checkpoint_ns TEXT NOT NULL DEFAULT '',\n    checkpoint_id TEXT NOT NULL,\n    parent_checkpoint_id TEXT,\n    type TEXT,\n    checkpoint JSONB NOT NULL,\n    metadata JSONB NOT NULL DEFAULT '{}',\n    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)\n);", "CREATE TABLE IF NOT EXISTS checkpoint_blobs (\n    thread_id TEXT NOT NULL,\n    checkpoint_ns TEXT NOT NULL DEFAULT '',\n    channel TEXT NOT NULL,\n    version TEXT NOT NULL,\n    type TEXT NOT NULL,\n    blob BYTEA,\n    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)\n);", "CREATE TABLE IF NOT EXISTS checkpoint_writes (\n    thread_id TEXT NOT NULL,\n    checkpoint_ns TEXT NOT NULL DEFAULT '',\n    checkpoint_id TEXT NOT NULL,\n    task_id TEXT NOT NULL,\n    idx INTEGER NOT NULL,\n    channel TEXT NOT NULL,\n    type TEXT,\n    blob BYTEA NOT NULL,\n    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)\n);", 'ALTER TABLE checkpoint_blobs ALTER COLUMN blob DROP not null;', 'SELECT 1;', '\n    CREATE INDEX IF NOT EXISTS checkpoints_thread_id_idx ON checkpoints(thread_id);\n    ', '\n    CREATE INDEX IF NOT EXISTS checkpoint_blobs_thread_id_idx ON checkpoint_blobs(thread_id);\n    ', '\n    CREATE INDEX IF NOT EXISTS checkpoint_writes_thread_id_idx ON checkpoint_writes(thread_id);\n    ', "ALTER TABLE checkpoint_writes ADD COLUMN IF NOT EXISTS task_path TEXT NOT NULL DEFAULT '';"]

def upgrade():
    op.execute('CREATE SCHEMA processing_graph')
    op.execute('SET LOCAL search_path = processing_graph, public')
    for version,statement in enumerate(STATEMENTS):
        op.execute(statement)
        op.execute(f'INSERT INTO checkpoint_migrations (v) VALUES ({version})')
    op.execute('SET LOCAL search_path = public')

def downgrade():
    for table in ('checkpoint_writes','checkpoint_blobs','checkpoints','checkpoint_migrations'):
        op.execute(f'DROP TABLE processing_graph.{table}')
    op.execute('DROP SCHEMA processing_graph')
