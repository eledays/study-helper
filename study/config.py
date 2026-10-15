import logging
from typing import Literal
from urllib.parse import urlsplit
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore', hide_input_in_errors=True)
    bot_token: str = ''
    bot_username: str = ''
    secret_key: str = ''
    database_url: str = 'sqlite:///data/study.db'
    base_url: str = 'http://localhost:8000'
    cookie_secure: bool = False
    debug: bool = False
    environment: Literal['development', 'production'] = 'development'

    @model_validator(mode='after')
    def production_safety(self):
        if self.environment == 'production':
            url = urlsplit(self.base_url)
            if self.debug or not self.cookie_secure:
                raise ValueError('Production требует DEBUG=false и COOKIE_SECURE=true')
            if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('', '/'):
                raise ValueError('Production требует BASE_URL вида https://study.example.com')
            if len(self.secret_key) < 48 or self.secret_key.startswith('replace-'):
                raise ValueError('Production требует случайный SECRET_KEY длиной от 48 символов')
            if not self.bot_token or not self.bot_username:
                raise ValueError('Production требует BOT_TOKEN и BOT_USERNAME')
            if not self.database_url.startswith('sqlite:///') or ':memory:' in self.database_url:
                raise ValueError('Production поддерживает файловую SQLite на локальном диске')
        return self

def setup_logging():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
