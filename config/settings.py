from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # API ключи
    anthropic_api_key: str = Field(..., alias="ANTHROPIC_API_KEY")

    # Модели
    llm_fast_model: str = Field("claude-haiku-4-5-20251001", alias="LLM_FAST_MODEL")
    llm_balanced_model: str = Field("claude-sonnet-4-6", alias="LLM_BALANCED_MODEL")
    llm_powerful_model: str = Field("claude-opus-4-6", alias="LLM_POWERFUL_MODEL")

    # Sandbox
    sandbox_timeout: int = Field(30, alias="SANDBOX_TIMEOUT")
    sandbox_cpu: float = Field(0.5, alias="SANDBOX_CPU")
    sandbox_memory: str = Field("256m", alias="SANDBOX_MEMORY")

    # Логи
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    log_dir: str = Field("data/logs", alias="LOG_DIR")


settings = Settings()