import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./hospital.db"

    admin_username: str = "admin"
    admin_password: str = "admin123"

    # SMS webhook — fired when a new appointment is created.
    # URL template supports {phone} and {message} placeholders (URL-encoded automatically).
    # e.g. https://sms.example.com/send?api_key=YOUR_KEY&to={phone}&msg={message}
    sms_enabled: bool = False
    sms_api_url: str = ""
    sms_method: str = "GET"  # GET or POST
    sms_timeout_seconds: int = 5

    class Config:
        env_file = ".env"


settings = Settings()
