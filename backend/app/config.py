from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal
from pydantic import model_validator
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / '.env', override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='../.env', extra='ignore')
    database_url: str = 'sqlite:///./dus.db'
    redis_url: str = 'redis://localhost:6379/0'
    task_mode: str = 'local'
    cookie_secure: bool = False
    cookie_samesite: Literal['strict', 'lax', 'none'] = 'strict'
    allowed_origins: str = 'http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000'
    admin_email: str = ''
    admin_password: str = ''
    demo_mode: bool = False

    @model_validator(mode='after')
    def secure_cross_site_cookie(self):
        if self.cookie_samesite == 'none' and not self.cookie_secure:
            raise ValueError('COOKIE_SAMESITE=none требует COOKIE_SECURE=true и HTTPS')
        return self


settings = Settings()
