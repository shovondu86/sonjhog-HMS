from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./hospital.db"

    # Login users are created by `python -m app.seed` (there is no registration endpoint).
    admin_username: str = "admin"
    admin_password: str = "admin123"
    # Optional second user with the read-mostly `agent` scope (the voice agent).
    agent_username: str = ""
    agent_password: str = ""

    hospital_name: str = "Hospital"
    # All `date`/`time` values are hospital-local; "today" is judged in this zone.
    timezone: str = "Asia/Dhaka"

    # SMS webhook — fired when a new appointment is created.
    # URL template supports {phone} and {message} placeholders (URL-encoded automatically).
    sms_enabled: bool = False
    sms_api_url: str = ""
    sms_method: str = "GET"  # GET or POST
    sms_timeout_seconds: int = 5


settings = Settings()
