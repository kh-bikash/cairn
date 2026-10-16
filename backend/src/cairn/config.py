"""Runtime configuration, loaded from environment variables and `.env`."""

from __future__ import annotations

import json
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# backend/src/cairn/config.py -> repo root is three levels above the package.
REPO_ROOT = Path(__file__).resolve().parents[3]

Provider = Literal["groq", "gemini", "openai", "anthropic", "typesafe"]

PROVIDER_BASE_URLS: dict[str, str] = {
    "groq": "https://api.groq.com/openai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1/",
    # Free, rate-limited (~40 req/min) but with no daily cap: the fallback that keeps answers flowing.
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    # Workers AI: the account id is part of the URL ({account} is filled from CLOUDFLARE_ACCOUNT_ID).
    "cloudflare": "https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1",
    # Unverified until we have console access; override with CAIRN_TYPESAFE_BASE_URL.
    "typesafe": "https://api.typesafe.ai/v1",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CAIRN_",
        env_file=(REPO_ROOT / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- FalkorDB -------------------------------------------------------------
    falkordb_host: str = "localhost"
    falkordb_port: int = 6379
    falkordb_password: SecretStr | None = None
    graph_name: str = "cairn"

    # --- Data sources ------------------------------------------------------------
    github_token: SecretStr | None = Field(default=None, validation_alias="GITHUB_TOKEN")
    repos: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "FalkorDB/FalkorDB",
            "FalkorDB/falkordb-py",
            "FalkorDB/falkordb-ts",
            "FalkorDB/falkordb-go",
            "FalkorDB/falkordb-rs",
            "FalkorDB/JFalkorDB",
            "FalkorDB/GraphRAG-SDK",
            "FalkorDB/QueryWeaver",
            "FalkorDB/FalkorDB-MCPServer",
            "FalkorDB/falkordb-browser",
            "FalkorDB/code-graph",
            "FalkorDB/text-to-cypher",
            "FalkorDB/docs",
        ]
    )
    since: str = "2025-01-01"
    max_items_per_repo: int = 600
    data_dir: Path = REPO_ROOT / "data"

    # --- LLM providers -------------------------------------------------------------
    groq_api_key: SecretStr | None = Field(default=None, validation_alias="GROQ_API_KEY")
    gemini_api_key: SecretStr | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    typesafe_api_key: SecretStr | None = Field(default=None, validation_alias="TYPESAFE_API_KEY")
    nvidia_api_key: SecretStr | None = Field(default=None, validation_alias="NVIDIA_API_KEY")
    openrouter_api_key: SecretStr | None = Field(default=None, validation_alias="OPENROUTER_API_KEY")
    cloudflare_api_key: SecretStr | None = Field(default=None, validation_alias=AliasChoices("CLOUDFLARE_API_KEY", "CLOUDFLARE_API_TOKEN"))
    cloudflare_account_id: str | None = Field(default=None, validation_alias="CLOUDFLARE_ACCOUNT_ID")

    # Model roles, each "provider:model".
    # Defaults match .env.example and the demo snapshot: the cache is keyed by model, so a keyless replay only
    # finds responses for the models that produced them.
    model_decide: str = ",".join(
        [
            "groq:openai/gpt-oss-20b",
            "groq:openai/gpt-oss-120b",
            "cloudflare:@cf/openai/gpt-oss-120b",
            "nvidia:nvidia/nemotron-3.5-super-vl-120b-a12b",
            "groq:qwen/qwen3.8-27b",
            "gemini:gemini-2.5-flash-lite",
            "openrouter:nvidia/nemotron-3.5-super-vl:free",
        ]
    )
    model_extract: str = ",".join(
        [
            "groq:openai/gpt-oss-120b",
            "cloudflare:@cf/openai/gpt-oss-120b",
            "nvidia:nvidia/nemotron-3.5-super-vl-120b-a12b",
            "groq:qwen/qwen3.8-27b",
            "gemini:gemini-3.5-flash",
            "gemini:gemini-2.5-flash",
            "openrouter:nvidia/nemotron-3.5-super-vl:free",
        ]
    )
    model_agent: str = ",".join(
        [
            "groq:openai/gpt-oss-120b",
            "cloudflare:@cf/openai/gpt-oss-120b",
            "nvidia:nvidia/nemotron-3.5-super-vl-120b-a12b",
            "groq:qwen/qwen3.8-27b",
            "gemini:gemini-2.5-flash",
            "gemini:gemini-3.8-flash",
            "openrouter:nvidia/nemotron-3.5-super-vl:free",
        ]
    )
    typesafe_base_url: str | None = None
    llm_cache: bool = True
    llm_max_concurrency: int = 4

    # --- Agent / API ---------------------------------------------------------------
    agent_max_steps: int = 5  # evidence is prefetched; more rounds mostly add latency and rate-limit pressure
    decide_confidence_threshold: float = 0.7
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:5173"])

    # Public deployment: when set, every write endpoint requires `Authorization: Bearer <token>`.
    admin_token: SecretStr | None = None
    ask_rate_limit_per_minute: int = 20

    # Writes (comments, wiki PRs) are only ever allowed against these repos.
    action_repos: Annotated[list[str], NoDecode] = Field(default_factory=list)
    wiki_repo: str | None = None

    @field_validator("repos", "cors_origins", "action_repos", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            if v.strip().startswith("["):
                return json.loads(v)
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "llm-cache"

    @property
    def since_ms(self) -> int:
        from datetime import UTC, datetime

        return int(datetime.fromisoformat(self.since).replace(tzinfo=UTC).timestamp() * 1000)

    def api_key_for(self, provider: str) -> str | None:
        secret: SecretStr | None = getattr(self, f"{provider}_api_key", None)
        return secret.get_secret_value() if secret else None

    def resolve_github_token(self) -> str | None:
        """Prefer GITHUB_TOKEN; fall back to the GitHub CLI's token for local development."""
        if self.github_token:
            return self.github_token.get_secret_value()
        gh = shutil.which("gh")
        if not gh:
            return None
        try:
            out = subprocess.run([gh, "auth", "token"], capture_output=True, text=True, timeout=10, check=True)
        except (subprocess.SubprocessError, OSError):
            return None
        return out.stdout.strip() or None


@lru_cache
def get_settings() -> Settings:
    return Settings()
