from pathlib import Path
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"
if ROOT_ENV.exists():
    load_dotenv(ROOT_ENV, override=False)

ENGINE_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_ENV) if ROOT_ENV.exists() else ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql://lakehouse:lakehouse_secret@localhost:5435/lakehouse_db"

    minio_endpoint: str = "http://localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "lakehouse"
    aws_region: str = "us-east-1"

    embedding_model: str = "gemini/gemini-embedding-001"
    embedding_dim: int = 768

    gemini_api_key: str = ""
    anthropic_api_key: str = ""
    deepseek_api_key: str = ""
    openai_api_key: str = ""
    groq_api_key: str = ""
    ollama_api_base: str = ""

    default_system_prompt: str = ""
    models_yaml_path: str = str(ENGINE_DIR / "models.yaml")

    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173"]


settings = Settings()
