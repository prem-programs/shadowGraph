"""Settings, read once from environment variables (and an optional .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv() -> None:
    """Load KEY=VALUE lines from .env files without overriding real env vars."""
    here = Path(__file__).resolve()
    for candidate in (Path.cwd() / ".env", here.parents[1] / ".env", here.parents[2] / ".env"):
        if not candidate.is_file():
            continue
        for raw in candidate.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv()


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return default if value is None or value.strip() == "" else value.strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    falkordb_url: str = field(default_factory=lambda: _env("FALKORDB_URL", "redis://localhost:6379"))
    tenants: tuple[str, ...] = field(
        default_factory=lambda: tuple(t.strip() for t in _env("TENANTS", "acme,globex").split(",") if t.strip())
    )
    embed_backend: str = field(default_factory=lambda: _env("EMBED_BACKEND", "auto"))  # auto | fastembed | hash
    embed_model: str = field(default_factory=lambda: _env("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2"))
    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY"))
    gemini_model: str = field(default_factory=lambda: _env("GEMINI_MODEL", "gemini-2.5-flash"))
    default_mode: str = field(default_factory=lambda: _env("DEFAULT_MODE", "rules"))  # rules | llm
    github_token: str = field(default_factory=lambda: _env("GITHUB_TOKEN"))
    github_repo: str = field(default_factory=lambda: _env("GITHUB_REPO"))  # owner/name
    github_webhook_secret: str = field(default_factory=lambda: _env("GITHUB_WEBHOOK_SECRET"))
    webhook_tenant: str = field(default_factory=lambda: _env("WEBHOOK_TENANT", "acme"))
    public_url: str = field(default_factory=lambda: _env("PUBLIC_URL", "http://localhost:8000"))
    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(o.strip() for o in _env("CORS_ORIGINS", "*").split(",") if o.strip())
    )
    sim_workers: int = field(default_factory=lambda: max(1, _env_int("SIM_WORKERS", 3)))
    pr_token_verify: int = field(default_factory=lambda: _env_int("PR_TOKEN_VERIFY", 1))
    pr_session_refresh: int = field(default_factory=lambda: _env_int("PR_SESSION_REFRESH", 2))
    pr_jwks: int = field(default_factory=lambda: _env_int("PR_JWKS", 3))
    static_dir: str = field(default_factory=lambda: _env("STATIC_DIR", ""))
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))

    @property
    def llm_available(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def github_enabled(self) -> bool:
        return bool(self.github_token and self.github_repo)


settings = Settings()
