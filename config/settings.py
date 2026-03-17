from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: str = Field(..., alias="ANTHROPIC_API_KEY")

    llm_fast_model: str = Field("claude-haiku-4-5-20251001", alias="LLM_FAST_MODEL")
    llm_balanced_model: str = Field("claude-sonnet-4-6", alias="LLM_BALANCED_MODEL")
    llm_powerful_model: str = Field("claude-opus-4-6", alias="LLM_POWERFUL_MODEL")

    sandbox_timeout: int = Field(30, alias="SANDBOX_TIMEOUT")
    sandbox_cpu: float = Field(0.5, alias="SANDBOX_CPU")
    sandbox_memory: str = Field("256m", alias="SANDBOX_MEMORY")

    log_level: str = Field("INFO", alias="LOG_LEVEL")
    log_dir: str = Field("data/logs", alias="LOG_DIR")

    telegram_bot_token: str = Field("", alias="TELEGRAM_BOT_TOKEN")
    telegram_allowed_users: str = Field("", alias="TELEGRAM_ALLOWED_USERS")
    openai_api_key: str = Field("", alias="OPENAI_API_KEY")
    openai_base_url: str = Field("", alias="OPENAI_BASE_URL")
    tavily_api_key: str = Field("", alias="TAVILY_API_KEY")
    database_url: str = Field("", alias="DATABASE_URL")
    artel_id: str = Field("default", alias="ARTEL_ID")
    # MCP server connections (JSON: [{"name": "...", "url": "...", "api_key": "..."}])
    mcp_servers: str = Field("", alias="MCP_SERVERS")
    # A2A peer agents (JSON: [{"name": "...", "url": "...", "api_key": "..."}])
    a2a_peers: str = Field("", alias="A2A_PEERS")
    # FIX-26: Memory retention period (days). Default 3 years — agent never forgets.
    memory_retention_days: int = Field(1095, alias="MEMORY_RETENTION_DAYS")
    # Error monitoring: dedicated bot token (falls back to main bot if empty)
    error_bot_token: str = Field("", alias="TELEGRAM_ERROR_BOT_TOKEN")
    # Error monitoring: Telegram chat ID for error notifications (separate from user bot)
    error_monitor_chat_id: str = Field("", alias="TELEGRAM_ERROR_CHAT_ID")
    error_monitor_interval: int = Field(60, alias="ERROR_MONITOR_INTERVAL")
    timezone: str = Field("Asia/Vladivostok", alias="TIMEZONE")

    @property
    def allowed_user_ids(self) -> list[int]:
        if not self.telegram_allowed_users:
            return []
        return [int(uid.strip()) for uid in self.telegram_allowed_users.split(",")]


settings = Settings()
