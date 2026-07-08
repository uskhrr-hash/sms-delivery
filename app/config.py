from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_name: str = 'SMS Delivery'
    debug: bool = False
    database_url: str = 'sqlite:///./data/sms_delivery.db'

    api_keys: str = 'booking:dev-key'
    admin_user: str = 'admin'
    admin_password: str = 'admin'

    send_delay_min: int = 3
    send_delay_max: int = 4

    smsgate_base_url: str = 'https://localhost'
    smsgate_username: str = ''
    smsgate_password: str = ''
    public_base_url: str = 'http://localhost:8000'
    callback_secret: str = ''

    worker_poll_sec: float = 3.0
    max_send_attempts: int = 3

    def parsed_api_keys(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for part in self.api_keys.split(','):
            part = part.strip()
            if not part or ':' not in part:
                continue
            name, key = part.split(':', 1)
            result[name.strip()] = key.strip()
        return result


@lru_cache
def get_settings() -> Settings:
    return Settings()
