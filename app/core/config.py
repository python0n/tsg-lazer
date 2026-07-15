"""Application configuration using pydantic-settings."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Server
    app_name: str = "tsg-lazer"
    debug: bool = False
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8001  # 8000 is bancho-py-ex

    # Database — MySQL (asyncmy driver)
    # Format: mysql+asyncmy://user:pass@host:port/dbname
    database_url: str = "mysql+asyncmy://tsg:changeme@localhost:3306/tsg_lazer"

    # Redis (shared with bancho-py-ex)
    redis_url: str = "redis://localhost:6379/1"  # DB 1, bancho uses 0

    # OAuth2 / JWT
    secret_key: str = "change-me-in-production-use-openssl-rand-hex-32"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 days
    refresh_token_expire_days: int = 30

    # OAuth2 Client (musi być zgodny z patche w kliencie lazer)
    oauth_client_id: str = "5"
    oauth_client_secret: str = "change-me"

    # External URLs
    api_base_url: str = "https://taksiegra.ovh"
    website_url: str = "https://taksiegra.ovh"

    # File storage
    beatmaps_path: str = "./data/beatmaps"
    replays_path: str = "./data/replays"
    avatars_path: str = "./data/avatars"
    covers_path: str = "./data/covers"
    avatar_max_mb: int = 5
    cover_max_mb: int = 10
    assets_base_url: str = "https://taksiegra.ovh"

    # Rate limiting
    rate_limit_requests: int = 1200
    rate_limit_window_seconds: int = 600  # 10 minutes

    # Server mode
    server_mode: Literal["development", "production"] = "development"

    # Beatmap mirror — nerinyan (ten sam co bancho-py-ex)
    beatmap_mirror_url: str = "https://api.nerinyan.moe"

    # True = mirror (nerinyan). False = oficjalne osu! API (wymagane do lookupu po checksum).
    use_beatmap_mirror: bool = True

    # Official osu! API v2 credentials (dla wyszukiwania beatmap)
    osu_api_client_id: str = ""
    osu_api_client_secret: str = ""


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()

# singleton alias
settings = get_settings()
