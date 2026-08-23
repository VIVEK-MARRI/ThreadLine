"""Application configuration.

Centralises all runtime settings so they can be driven by environment
variables or a .env file later, without touching any other module.
"""

from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Threadline application settings."""

    app_name: str = "Threadline"
    app_version: str = "0.1.0"
    api_v1_prefix: str = "/api/v1"
    debug: bool = False

    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )


# Module-level singleton – import this everywhere instead of instantiating Settings again.
settings = Settings()
