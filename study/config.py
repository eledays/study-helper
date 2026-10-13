import logging
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    bot_token: str = ''
    bot_username: str = ''
    secret_key: str = ''
    database_url: str = 'sqlite:///data/study.db'
    base_url: str = 'http://localhost:8000'
    cookie_secure: bool = False

def setup_logging():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
