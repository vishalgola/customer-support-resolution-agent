from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    jwt_secret: str = "dev-only-change-me"
    jwt_algorithm: str = "HS256"
    payment_provider_url: str = "http://localhost:8001"
    payment_timeout_seconds: float = 5.0
    llm_model: str = ""
    policy_min_score: float = 0.0
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()