from pathlib import Path
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"
if ROOT_ENV.exists():
    load_dotenv(ROOT_ENV, override=False)


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
    openai_api_key: str = ""

    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173"]

    @property
    def delta_storage_options(self) -> dict[str, str]:
        return {
            "AWS_ACCESS_KEY_ID": self.minio_access_key,
            "AWS_SECRET_ACCESS_KEY": self.minio_secret_key,
            "AWS_ENDPOINT_URL": self.minio_endpoint,
            "AWS_REGION": self.aws_region,
            "AWS_ALLOW_HTTP": "true" if self.minio_endpoint.startswith("http://") else "false",
            "AWS_S3_ALLOW_UNSAFE_RENAME": "true",
        }


settings = Settings()
