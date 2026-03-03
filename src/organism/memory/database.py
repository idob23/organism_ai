from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Column, String, Float, Integer, Text, DateTime, Boolean, Index
from sqlalchemy import func, text
from pgvector.sqlalchemy import Vector
from config.settings import settings


class Base(DeclarativeBase):
    pass


class TaskMemory(Base):
    __tablename__ = "task_memories"

    id = Column(String, primary_key=True)
    task = Column(Text, nullable=False)
    result = Column(Text, nullable=False)
    success = Column(Boolean, default=True)
    duration = Column(Float, default=0.0)
    steps_count = Column(Integer, default=0)
    tools_used = Column(Text, default="")
    quality_score = Column(Float, default=0.0)  # NEW: 0.0 - 1.0
    embedding = Column(Vector(1536), nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class UserProfile(Base):
    __tablename__ = "user_profile"

    id = Column(String, primary_key=True)          # UUID, generated on insert
    key = Column(String, nullable=False, index=True)  # fact_type, e.g. "name"
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    valid_from = Column(DateTime, server_default=func.now())
    valid_until = Column(DateTime, nullable=True)   # NULL = currently active
    superseded_by = Column(String, nullable=True)   # id of the row that replaced this one


class SolutionCacheEntry(Base):
    __tablename__ = "solution_cache"

    task_hash = Column(String(64), primary_key=True)   # SHA-256 hex
    canonical_task = Column(Text, nullable=False)
    original_task = Column(Text, nullable=False)
    result = Column(Text, nullable=False)
    quality_score = Column(Float, default=0.0)
    hits = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())
    expires_at = Column(DateTime, nullable=False)


class AgentReflection(Base):
    __tablename__ = "agent_reflections"

    id = Column(String, primary_key=True)
    agent_name = Column(String, nullable=False)
    task_hash = Column(String(16), nullable=False, index=True)
    score = Column(Integer, nullable=False)   # 1-5
    insight = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    # Q-7.1: structured reflection fields
    failure_type = Column(String, nullable=True)      # tool_error|plan_error|llm_error|timeout|validation|unknown|none
    root_cause = Column(Text, nullable=True)
    corrective_action = Column(Text, nullable=True)
    reflection_confidence = Column(Float, nullable=True)  # 0.0-1.0


class KnowledgeRule(Base):
    __tablename__ = "knowledge_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_text = Column(Text, nullable=False)
    confidence = Column(Float, default=0.8)
    source_tasks = Column(Text, default="")  # comma-separated task hashes
    usage_count = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    valid_from = Column(DateTime, server_default=func.now())
    valid_until = Column(DateTime, nullable=True)   # NULL = currently active


class ProceduralTemplate(Base):
    __tablename__ = "procedural_templates"

    id = Column(String, primary_key=True)
    pattern_name = Column(String, nullable=False, index=True)  # e.g. "csv_report"
    tools_sequence = Column(Text, nullable=False)               # JSON: ["code_executor"]
    code_template = Column(Text, nullable=True)                 # code skeleton if applicable
    task_pattern = Column(Text, nullable=False)                 # generic task description
    success_count = Column(Integer, default=1)
    avg_quality = Column(Float, default=0.0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class FewShotExample(Base):
    __tablename__ = "few_shot_examples"

    id = Column(String, primary_key=True)                    # uuid
    task_type = Column(String, nullable=False, index=True)   # writing/code/research/data/presentation/mixed
    task_text = Column(Text, nullable=False)                  # original user task (truncated to 300 chars)
    plan_json = Column(Text, nullable=False)                  # JSON: [{"tool": "...", "description": "..."}]
    quality_score = Column(Float, nullable=False)
    tools_used = Column(Text, nullable=False)                 # comma-separated: "code_executor,web_search"
    embedding = Column(Vector(1536), nullable=True)           # for semantic similarity search
    usage_count = Column(Integer, default=0)                  # how many times injected as few-shot
    created_at = Column(DateTime, server_default=func.now())


class MemoryEdge(Base):
    __tablename__ = "memory_edges"
    __table_args__ = (
        Index("ix_edges_from_type", "from_id", "edge_type"),
        Index("ix_edges_to_type", "to_id", "edge_type"),
    )

    id = Column(String, primary_key=True)          # uuid
    from_id = Column(String, nullable=False)        # task_memories.id or user_profile.id
    to_id = Column(String, nullable=False)
    edge_type = Column(String, nullable=False)      # temporal|causal|entity|procedural
    weight = Column(Float, default=1.0)
    meta_json = Column("metadata", Text, nullable=True)  # JSON string; 'metadata' reserved by SA
    created_at = Column(DateTime, server_default=func.now())


class PromptVersion(Base):
    __tablename__ = "prompt_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    prompt_name = Column(String, nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1)
    content = Column(Text, nullable=False)
    avg_quality = Column(Float, default=0.0)
    task_count = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())
    is_active = Column(Boolean, default=False, nullable=False)


engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Q-2.2: GIN index for Russian full-text search (BM25 hybrid search).
        # Safe to run on existing DBs — IF NOT EXISTS makes it idempotent.
        # Manual migration SQL (if needed outside init_db):
        #   CREATE INDEX IF NOT EXISTS idx_task_memories_task_fts
        #   ON task_memories USING GIN (to_tsvector('russian', task));
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_task_memories_task_fts "
            "ON task_memories USING GIN (to_tsvector('russian', task))"
        ))
        # Q-5.1: Temporal fact tracking — ADD COLUMN IF NOT EXISTS migrations.
        # Each statement is independent so a single failure does not block others.
        _migrations_51 = [
            # user_profile: new columns
            "ALTER TABLE user_profile ADD COLUMN IF NOT EXISTS"
            " id VARCHAR DEFAULT gen_random_uuid()::text",
            "ALTER TABLE user_profile ADD COLUMN IF NOT EXISTS"
            " valid_from TIMESTAMP DEFAULT NOW()",
            "ALTER TABLE user_profile ADD COLUMN IF NOT EXISTS"
            " valid_until TIMESTAMP",
            "ALTER TABLE user_profile ADD COLUMN IF NOT EXISTS"
            " superseded_by VARCHAR",
            # knowledge_rules: new columns
            "ALTER TABLE knowledge_rules ADD COLUMN IF NOT EXISTS"
            " valid_from TIMESTAMP DEFAULT NOW()",
            "ALTER TABLE knowledge_rules ADD COLUMN IF NOT EXISTS"
            " valid_until TIMESTAMP",
        ]
        for ddl in _migrations_51:
            try:
                await conn.execute(text(ddl))
            except Exception:
                pass
        # Migrate user_profile PK from 'key' to 'id' on existing DBs.
        # On fresh DBs create_all already used 'id' as PK, so the DO block
        # detects that and skips the constraint change safely.
        try:
            await conn.execute(text(
                "UPDATE user_profile SET id = gen_random_uuid()::text WHERE id IS NULL"
            ))
            await conn.execute(text(
                "ALTER TABLE user_profile ALTER COLUMN id SET NOT NULL"
            ))
            await conn.execute(text("""
                DO $$ BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.key_column_usage
                        WHERE table_name = 'user_profile'
                          AND constraint_name = 'user_profile_pkey'
                          AND column_name = 'key'
                    ) THEN
                        ALTER TABLE user_profile DROP CONSTRAINT user_profile_pkey;
                        ALTER TABLE user_profile ADD PRIMARY KEY (id);
                    END IF;
                END $$
            """))
        except Exception:
            pass
        # Q-7.1: Structured reflection columns — idempotent migration.
        _migrations_71 = [
            "ALTER TABLE agent_reflections ADD COLUMN IF NOT EXISTS failure_type VARCHAR",
            "ALTER TABLE agent_reflections ADD COLUMN IF NOT EXISTS root_cause TEXT",
            "ALTER TABLE agent_reflections ADD COLUMN IF NOT EXISTS corrective_action TEXT",
            "ALTER TABLE agent_reflections ADD COLUMN IF NOT EXISTS reflection_confidence FLOAT",
        ]
        for ddl in _migrations_71:
            try:
                await conn.execute(text(ddl))
            except Exception:
                pass
        # Q-7.3: Few-shot examples — idempotent migration for future column additions.
        _migrations_73 = [
            "ALTER TABLE few_shot_examples ADD COLUMN IF NOT EXISTS usage_count INTEGER DEFAULT 0",
        ]
        for ddl in _migrations_73:
            try:
                await conn.execute(text(ddl))
            except Exception:
                pass