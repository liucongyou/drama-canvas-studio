from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

AGENT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    canvas_base_url: str = "http://127.0.0.1:8890"
    canvas_tenant_id: str = "owner"
    canvas_tenant_slug: str = "owner"
    canvas_cookie: str = ""
    canvas_token: str = ""
    agent_internal_secret: str = ""
    agent_sidecar_url: str = "http://127.0.0.1:8895"
    agent_gateway_enabled: bool = True
    agent_signature_window_seconds: int = 60
    agent_max_tool_steps: int = 12
    agent_tool_result_bytes: int = 32 * 1024
    db_path: Path = AGENT_DIR / "data" / "agent.db"
    skills_dir: Path = AGENT_DIR / "data" / "skills"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_mode: str = "auto"  # auto | mock | openai
    request_timeout_seconds: float = 180.0
    max_entry_chars: int = 24_000
    max_zip_bytes: int = 50 * 1024 * 1024
    max_zip_files: int = 2000
    max_repair_rounds: int = 3

    model_config = SettingsConfigDict(env_file=AGENT_DIR / ".env", extra="ignore")

    @property
    def use_mock_llm(self) -> bool:
        return self.llm_mode == "mock" or not (self.llm_base_url and self.llm_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
