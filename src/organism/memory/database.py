from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Column, String, Float, Integer, Text, DateTime, Boolean, Index
from sqlalchemy import func, text
from pgvector.sqlalchemy import Vector
from config.settings import settings
from src.organism.logging.error_handler import get_logger

_log = get_logger("memory.database")


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
    user_id = Column(String, nullable=False, default="default")  # Telegram user ID
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


class PromptPopulationMember(Base):
    __tablename__ = "prompt_population"

    id = Column(Integer, primary_key=True, autoincrement=True)
    prompt_name = Column(String, nullable=False, index=True)
    content = Column(Text, nullable=False)
    generation = Column(Integer, default=0)
    fitness = Column(Float, default=0.0)
    eval_count = Column(Integer, default=0)
    parent_id = Column(Integer, nullable=True)
    mutation_type = Column(String, nullable=True)  # rephrase|restructure|specialize
    is_active = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class ErrorLog(Base):
    __tablename__ = "error_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    level = Column(String, nullable=False, default="ERROR")       # ERROR, WARNING, CRITICAL
    component = Column(String, nullable=False)                     # core.loop, agents.coder, tools.web_fetch, etc.
    message = Column(Text, nullable=False)                         # Error message
    traceback = Column(Text, nullable=True)                        # Full traceback
    task_id = Column(String, nullable=True)                        # Related task_id if available
    task_text = Column(Text, nullable=True)                        # Task text for context (first 500 chars)
    artel_id = Column(String, default="default")                   # For multi-tenancy
    notified = Column(Boolean, default=False)                      # Has this been sent to Telegram?
    created_at = Column(DateTime, server_default=func.now())


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, nullable=False, index=True)
    role = Column(String, nullable=False)         # "user" or "assistant"
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class SchemaMigration(Base):
    __tablename__ = "schema_migrations"

    version = Column(Integer, primary_key=True)       # Migration number (1, 2, 3...)
    name = Column(String, nullable=False)              # Human-readable name
    applied_at = Column(DateTime, server_default=func.now())


engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    """Initialize database: create tables, apply pending migrations."""
    async with engine.begin() as conn:
        # 1. Create all tables (idempotent -- skips existing)
        await conn.run_sync(Base.metadata.create_all)

        # 2. Get applied migration versions
        try:
            result = await conn.execute(text(
                "SELECT version FROM schema_migrations ORDER BY version"
            ))
            applied = {row[0] for row in result.fetchall()}
        except Exception:
            applied = set()

        # 3. Run pending migrations
        for version, name, fn in _MIGRATIONS:
            if version not in applied:
                _log.info("Applying migration %d: %s", version, name)
                try:
                    await fn(conn)
                    await conn.execute(text(
                        "INSERT INTO schema_migrations (version, name) VALUES (:v, :n)"
                    ), {"v": version, "n": name})
                    _log.info("Migration %d applied successfully", version)
                except Exception as e:
                    _log.error("Migration %d failed: %s", version, e)
                    # Continue to next migration -- don't block


# -- Migration functions (each is idempotent) ---------------------

async def _m001_base_indexes(conn) -> None:
    """Performance indexes for core queries."""
    stmts = [
        # task_memories: main search index
        "CREATE INDEX IF NOT EXISTS ix_tm_created_at ON task_memories (created_at DESC)",
        # task_memories: filter for search_similar (success + quality + date)
        "CREATE INDEX IF NOT EXISTS ix_tm_search ON task_memories (success, quality_score, created_at DESC) WHERE success = true",
        # task_memories: GIN for Russian full-text search (BM25)
        "CREATE INDEX IF NOT EXISTS idx_task_memories_task_fts ON task_memories USING GIN (to_tsvector('russian', task))",
        # solution_cache: cleanup by expires_at
        "CREATE INDEX IF NOT EXISTS ix_sc_expires ON solution_cache (expires_at)",
        # solution_cache: lookup by hits for stats
        "CREATE INDEX IF NOT EXISTS ix_sc_hits ON solution_cache (hits DESC)",
        # agent_reflections: filter by agent_name (cross-agent Q-7.5)
        "CREATE INDEX IF NOT EXISTS ix_ar_agent ON agent_reflections (agent_name, created_at DESC)",
        # agent_reflections: score >= 3 for insights
        "CREATE INDEX IF NOT EXISTS ix_ar_score ON agent_reflections (score DESC, created_at DESC)",
        # knowledge_rules: active rules lookup
        "CREATE INDEX IF NOT EXISTS ix_kr_active ON knowledge_rules (valid_until, confidence DESC) WHERE valid_until IS NULL",
        # user_profile: active facts
        "CREATE INDEX IF NOT EXISTS ix_up_active ON user_profile (key, valid_until) WHERE valid_until IS NULL",
        # prompt_versions: active version lookup
        "CREATE INDEX IF NOT EXISTS ix_pv_active ON prompt_versions (prompt_name, is_active) WHERE is_active = true",
        # procedural_templates: pattern lookup
        "CREATE INDEX IF NOT EXISTS ix_pt_quality ON procedural_templates (avg_quality DESC, success_count DESC)",
        # few_shot_examples: quality lookup
        """DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'few_shot_examples') THEN
                EXECUTE 'CREATE INDEX IF NOT EXISTS ix_fse_quality ON few_shot_examples (quality_score DESC)';
            END IF;
        END $$""",
        # prompt_population: active members
        """DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'prompt_population') THEN
                EXECUTE 'CREATE INDEX IF NOT EXISTS ix_pp_active ON prompt_population (prompt_name, is_active, fitness DESC)';
            END IF;
        END $$""",
    ]
    for sql in stmts:
        try:
            await conn.execute(text(sql))
        except Exception:
            pass


async def _m002_artel_id(conn) -> None:
    """Add artel_id column to key tables for multi-tenancy readiness.

    Default='default'. Nullable. Does NOT enforce FK -- artels are configured
    externally. When multi-tenancy is enabled, queries filter by artel_id.
    Until then, column exists but is ignored.
    """
    tables = [
        "task_memories",
        "solution_cache",
        "agent_reflections",
        "user_profile",
        "knowledge_rules",
        "procedural_templates",
    ]
    for table in tables:
        try:
            await conn.execute(text(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS artel_id VARCHAR DEFAULT 'default'"
            ))
        except Exception:
            pass

    # Indexes for artel_id filtering (future multi-tenancy)
    for table in tables:
        try:
            await conn.execute(text(
                f"CREATE INDEX IF NOT EXISTS ix_{table[:10]}_artel ON {table} (artel_id)"
            ))
        except Exception:
            pass


async def _m003_error_log(conn) -> None:
    """Create error_log table for error monitoring (Telegram notifications).

    create_all() already handles this if ErrorLog model exists,
    but explicit CREATE IF NOT EXISTS is safer for migration ordering.
    """
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS error_log (
            id SERIAL PRIMARY KEY,
            level VARCHAR NOT NULL DEFAULT 'ERROR',
            component VARCHAR NOT NULL,
            message TEXT NOT NULL,
            traceback TEXT,
            task_id VARCHAR,
            task_text TEXT,
            artel_id VARCHAR DEFAULT 'default',
            notified BOOLEAN DEFAULT false,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """))
    await conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_el_unnotified ON error_log (notified, created_at DESC) WHERE notified = false"
    ))
    await conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_el_component ON error_log (component, created_at DESC)"
    ))
    await conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_el_level ON error_log (level, created_at DESC)"
    ))


async def _m004_structured_reflections(conn) -> None:
    """Ensure agent_reflections has Q-7.1 structured columns.

    Sprint 7 added these via _migrations_71, but they may be missing
    on DBs created before Sprint 7. Idempotent.
    """
    cols = [
        ("failure_type", "VARCHAR"),
        ("root_cause", "TEXT"),
        ("corrective_action", "TEXT"),
        ("reflection_confidence", "FLOAT DEFAULT 0.5"),
    ]
    for col_name, col_type in cols:
        try:
            await conn.execute(text(
                f"ALTER TABLE agent_reflections ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
            ))
        except Exception:
            pass


async def _m005_result_size(conn) -> None:
    """Add result_hash column for deduplication of large results."""
    try:
        await conn.execute(text(
            "ALTER TABLE task_memories ADD COLUMN IF NOT EXISTS result_hash VARCHAR(64)"
        ))
    except Exception:
        pass


async def _m006_retention_helpers(conn) -> None:
    """Create helper functions for data retention/cleanup.

    These are PostgreSQL functions callable via: SELECT cleanup_expired_cache();
    """
    # Clean expired solution cache entries
    await conn.execute(text("""
        CREATE OR REPLACE FUNCTION cleanup_expired_cache()
        RETURNS INTEGER AS $$
        DECLARE deleted INTEGER;
        BEGIN
            DELETE FROM solution_cache WHERE expires_at < NOW();
            GET DIAGNOSTICS deleted = ROW_COUNT;
            RETURN deleted;
        END;
        $$ LANGUAGE plpgsql;
    """))

    # Archive old reflections (keep last N per agent)
    await conn.execute(text("""
        CREATE OR REPLACE FUNCTION cleanup_old_reflections(keep_count INTEGER DEFAULT 1000)
        RETURNS INTEGER AS $$
        DECLARE deleted INTEGER;
        BEGIN
            WITH ranked AS (
                SELECT id, ROW_NUMBER() OVER (PARTITION BY agent_name ORDER BY created_at DESC) as rn
                FROM agent_reflections
            )
            DELETE FROM agent_reflections WHERE id IN (
                SELECT id FROM ranked WHERE rn > keep_count
            );
            GET DIAGNOSTICS deleted = ROW_COUNT;
            RETURN deleted;
        END;
        $$ LANGUAGE plpgsql;
    """))

    # Cleanup old error_log entries (keep last N days, only notified)
    await conn.execute(text("""
        CREATE OR REPLACE FUNCTION cleanup_old_errors(days INTEGER DEFAULT 30)
        RETURNS INTEGER AS $$
        DECLARE deleted INTEGER;
        BEGIN
            DELETE FROM error_log WHERE created_at < NOW() - (days || ' days')::INTERVAL AND notified = true;
            GET DIAGNOSTICS deleted = ROW_COUNT;
            RETURN deleted;
        END;
        $$ LANGUAGE plpgsql;
    """))

    # Archive old memory_edges (keep last N)
    await conn.execute(text("""
        CREATE OR REPLACE FUNCTION cleanup_old_edges(keep_count INTEGER DEFAULT 5000)
        RETURNS INTEGER AS $$
        DECLARE deleted INTEGER;
        BEGIN
            WITH ranked AS (
                SELECT id, ROW_NUMBER() OVER (ORDER BY created_at DESC) as rn
                FROM memory_edges
            )
            DELETE FROM memory_edges WHERE id IN (
                SELECT id FROM ranked WHERE rn > keep_count
            );
            GET DIAGNOSTICS deleted = ROW_COUNT;
            RETURN deleted;
        END;
        $$ LANGUAGE plpgsql;
    """))


async def _m007_few_shot_indexes(conn) -> None:
    """Ensure few_shot_examples and prompt_population have proper indexes."""
    stmts = [
        # few_shot_examples
        """DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'few_shot_examples') THEN
                EXECUTE 'CREATE INDEX IF NOT EXISTS ix_fse_task_type ON few_shot_examples (task_type, quality_score DESC)';
                EXECUTE 'CREATE INDEX IF NOT EXISTS ix_fse_created ON few_shot_examples (created_at DESC)';
            END IF;
        END $$""",
        # prompt_population
        """DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'prompt_population') THEN
                EXECUTE 'CREATE INDEX IF NOT EXISTS ix_pp_generation ON prompt_population (prompt_name, generation DESC)';
            END IF;
        END $$""",
    ]
    for sql in stmts:
        try:
            await conn.execute(text(sql))
        except Exception:
            pass


async def _m008_user_id_profile(conn) -> None:
    """Add user_id to user_profile for multi-user isolation (USER-1)."""
    try:
        await conn.execute(text(
            "ALTER TABLE user_profile ADD COLUMN IF NOT EXISTS user_id VARCHAR DEFAULT 'default'"
        ))
    except Exception:
        pass
    try:
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_up_user_key ON user_profile (user_id, key) WHERE valid_until IS NULL"
        ))
    except Exception:
        pass


async def _m009_chat_history(conn) -> None:
    """Create chat_messages table for conversation history."""
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id SERIAL PRIMARY KEY,
            user_id VARCHAR NOT NULL,
            role VARCHAR NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """))
    await conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_cm_user_time ON chat_messages (user_id, created_at DESC)"
    ))


# Migration registry -- (version, name, function)
# APPEND ONLY -- never remove or reorder entries
_MIGRATIONS = [
    (1, "base_indexes", _m001_base_indexes),
    (2, "artel_id", _m002_artel_id),
    (3, "error_log", _m003_error_log),
    (4, "structured_reflections", _m004_structured_reflections),
    (5, "result_size", _m005_result_size),
    (6, "retention_helpers", _m006_retention_helpers),
    (7, "few_shot_indexes", _m007_few_shot_indexes),
    (8, "user_id_profile", _m008_user_id_profile),
    (9, "chat_history", _m009_chat_history),
]
