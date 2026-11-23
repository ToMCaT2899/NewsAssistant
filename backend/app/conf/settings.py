"""仅从环境变量加载应用配置，密钥不得进入模型输入。"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")
    app_env: str = "development"
    database_url: str = "sqlite:///./data/news_assistant.db"
    jwt_secret: SecretStr = SecretStr("")
    model_encryption_key: SecretStr = SecretStr("")
    token_minutes: int = Field(60, ge=5, le=1440)
    admin_email: str = ""
    admin_password: SecretStr = SecretStr("")
    verified_recipient_email: str = ""
    llm_provider: str = "chat_completions"
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_public_endpoint: bool = False
    agent_max_model_calls: int = Field(15, ge=1, le=100)
    agent_max_tool_calls: int = Field(20, ge=1, le=100)
    agent_max_seconds: int = Field(180, ge=1, le=900)
    tool_timeout_seconds: int = Field(15, ge=1, le=60)
    agent_workspace_root: Path = Path("./workspace")
    shell_tool_enabled: bool = False
    rss_sources_json: str = (
        '{"huggingface":"https://huggingface.co/blog/feed.xml",'
        '"arxiv-ai":"https://rss.arxiv.org/rss/cs.AI",'
        '"openai":"https://openai.com/news/rss.xml",'
        '"techcrunch-ai":"https://techcrunch.com/category/artificial-intelligence/feed/"}'
    )
    smtp_host: str = "smtp.qq.com"
    smtp_port: int = Field(465, ge=1, le=65535)
    smtp_username: str = ""
    smtp_auth_code: SecretStr = SecretStr("")
    smtp_from_name: str = "AI 新闻简报"
    smtp_timeout_seconds: int = Field(20, ge=1, le=60)
    mail_max_attempts: int = Field(3, ge=1, le=5)
    worker_poll_seconds: int = Field(30, ge=1, le=300)
    lease_seconds: int = Field(360, ge=30, le=3600)

    @property
    def model_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_model and self.llm_api_key.get_secret_value())

    @property
    def mail_configured(self) -> bool:
        return bool(self.smtp_username and self.smtp_auth_code.get_secret_value())

    @property
    def root(self) -> Path:
        return Path(__file__).resolve().parents[3]


@lru_cache
def get_settings() -> Settings:
    return Settings()
