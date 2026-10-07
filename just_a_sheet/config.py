"""Configuração via variáveis de ambiente."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    spreadsheet_id: str
    drive_folder_id: str
    google_application_credentials: str = "/run/secrets/google-sa.json"
    llm_base_url: str = "https://ollama.com/v1"
    llm_api_key: str
    llm_model: str = "gpt-oss:20b"
    poll_seconds: int = 600
    tz: str = "America/Sao_Paulo"
