from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_api_key: str = "change-me"
    database_url: str = "sqlite+aiosqlite:///./data/concert-watcher.db"
    ticketmaster_api_key: str = ""
    default_countries: str = "PL"

    digest_api_url: str = "http://127.0.0.1:8182"
    digest_country_codes: str = "PL"
    digest_sources: str = "ticketmaster,knockout,fource"
    digest_limit: int = 200
    digest_http_timeout_seconds: int = 600

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_to: str = ""
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False

    http_timeout_seconds: int = 20
    ticketmaster_http_timeout_seconds: int = 120
    crawler_min_delay_seconds: float = 2.0
    crawler_user_agent: str = "ConcertWatcher/1.0"

    source_ticketmaster_enabled: bool = True
    source_knockout_enabled: bool = True
    source_fource_enabled: bool = True
    source_ebilet_enabled: bool = False
    source_eventim_enabled: bool = False
    source_going_enabled: bool = False
    source_livenation_enabled: bool = False

    @property
    def default_country_list(self) -> list[str]:
        return [c.strip().upper() for c in self.default_countries.split(",") if c.strip()]

    @property
    def smtp_recipient_list(self) -> list[str]:
        return [address.strip() for address in self.smtp_to.split(",") if address.strip()]


settings = Settings()
