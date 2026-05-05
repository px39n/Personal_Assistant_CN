"""应用配置管理，基于 pydantic-settings 从环境变量加载。"""

from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM 配置
    llm_provider: str = "openai"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    llm_fast_model: str = "gpt-4o-mini"

    # 数据库
    database_url: str = "postgresql+asyncpg://assistant:assistant@localhost:5432/assistant_cn"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # 搜索
    searxng_url: str = "http://localhost:8888"

    # 应用
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_debug: bool = False
    app_secret_key: str = "change-me-in-production"
    memory_mode: str = "persistent"  # "memory" 或 "persistent"

    # 金融数据
    tushare_token: Optional[str] = None
    em_proxy_url: Optional[str] = None  # Cloudflare Worker 代理, e.g. "https://em-proxy.xxx.workers.dev"

    # 企业微信
    wecom_corp_id: Optional[str] = None
    wecom_agent_id: Optional[int] = None
    wecom_secret: Optional[str] = None
    wecom_token: Optional[str] = None
    wecom_encoding_aes_key: Optional[str] = None

    # 飞书
    feishu_app_id: Optional[str] = None
    feishu_app_secret: Optional[str] = None
    feishu_verification_token: Optional[str] = None
    feishu_encrypt_key: Optional[str] = None


settings = Settings()

# 单用户模式：所有渠道的 user_id 统一映射到这个 ID
DEFAULT_USER_ID = "default_user"
