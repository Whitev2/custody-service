import logging
import os

from os import getenv

from pydantic import BaseModel
from pydantic_settings import BaseSettings

from .logger_init import setup_logging

setup_logging(getenv("LOG_LEVEL", "DEBUG"))

# Единый логгер для всего приложения
log = logging.getLogger("app")


def load_private_key_from_file() -> str | None:
    """Load private key from file (only for local mode)."""

    # 1. Try PRIVATE_KEY_FILE env var first
    key_file = getenv("PRIVATE_KEY_FILE", "")
    if key_file and os.path.exists(key_file):
        try:
            with open(key_file, "r") as f:
                return f.read().strip()
        except Exception as e:
            logging.warning(f"Failed to load private key from file {key_file}: {e}")

    # 2. Try the default local location
    default_locations = [
        "secrets/fireblocks.key",
    ]

    for loc in default_locations:
        if os.path.exists(loc):
            try:
                with open(loc, "r") as f:
                    key = f.read().strip()
                    if key:
                        logging.info(f"✅ Loaded Fireblocks private key from {loc}")
                        return key
            except Exception as e:
                logging.warning(f"Failed to load private key from {loc}: {e}")

    # 3. Fall back to environment variable
    key = getenv("PRIVATE_KEY", "")
    # Handle escaped newlines
    return key.replace("\\n", "\n") if key else None


class VaultSettings(BaseModel):
    """Настройки Vault credentials refresh"""

    # Интервал проверки TTL токена Vault (в секундах)
    TOKEN_CHECK_INTERVAL: int = 5 * 60  # 5 минут

    # Порог до истечения токена, при котором обновляем (в секундах)
    TOKEN_REFRESH_THRESHOLD: int = 10 * 60  # 10 минут

    # Интервал проверки TTL DB credentials (в секундах)
    DB_CREDENTIALS_CHECK_INTERVAL: int = 5 * 60  # 5 минут

    # Порог до истечения DB credentials, при котором обновляем (в секундах)
    DB_CREDENTIALS_REFRESH_THRESHOLD: int = 10 * 60  # 10 минут


class AppSettings(BaseModel):
    """Application settings."""

    LOG_LEVEL: str = getenv("LOG_LEVEL", "INFO")
    STAND: str = getenv("STAND", "local")
    API_KEY: str = ""
    PRIVATE_KEY: str = ""
    DEFAULT_PROVIDER: str = getenv("DEFAULT_PROVIDER", "fireblocks")
    BACKEND_URL: str = getenv("BACKEND_URL", "http://localhost:8000")

    def __init__(self, **data):
        super().__init__(**data)
        stand = getenv("STAND", "local")
        migrate_stand = getenv("MIGRATE_STAND")
        self.STAND = stand

        if stand == "local":
            logging.warning("⚠️ Запуск в режиме local")
            # Для local читаем из файла или env
            self.API_KEY = getenv("API_KEY", "")
            self.PRIVATE_KEY = load_private_key_from_file() or getenv("PRIVATE_KEY", "")
        elif migrate_stand:
            # Миграция - секреты custody не нужны
            logging.info("✅ Custody migration mode: skipping custody secrets")
        else:
            # Dev/Prod - читаем из Vault
            # {VAULT_KV_MOUNT}/{VAULT_SECRET_BASE}/{stand}/custody
            try:
                from app.services.vault_client import vault_client

                secrets = vault_client.get_secret("custody")
                self.API_KEY = secrets.get("API_KEY", "")
                self.PRIVATE_KEY = secrets.get("PRIVATE_KEY", "")
                logging.info(f"✅ AppSettings: секреты загружены из Vault ({stand})")
            except Exception as e:
                logging.error(
                    f"❌ Невозможно загрузить секреты из Vault ({stand}): {e}"
                )
                raise

    @property
    def is_testnet(self) -> bool:
        """Флаг тестовой сети для блокчейн-операций.

        True  используем тестовые сети (dev/local).
        False используем mainnet (prod и другие стенды).
        """
        return self.STAND in ("dev", "local")


class DatabaseSettings(BaseModel):
    """
    Настройки БД.

    Для STAND=local: использует статичные credentials из env vars
    Для STAND=dev/prod: использует динамические credentials из Vault Database Secrets Engine
    (credentials получаются автоматически в DatabaseManager)
    """

    HOST: str = getenv("CUSTODY_DB_HOST", "localhost")
    PORT: int = int(getenv("CUSTODY_DB_PORT", "5432"))
    USER: str = getenv("CUSTODY_DB_USER", "postgres")
    PASSWORD: str = getenv("CUSTODY_DB_PASSWORD", "postgres")
    NAME: str = getenv("CUSTODY_DB_NAME", "pg_custody")

    def __init__(self, **data):
        super().__init__(**data)
        stand = getenv("STAND", "local")
        migrate_stand = getenv("MIGRATE_STAND")

        if stand == "local":
            logging.warning("⚠️ Используем локальные переменные для подключения к БД")
        elif migrate_stand:
            # Режим миграции - название БД берём из env (alembic/env.py сам получит из Vault)
            logging.info("⚠️ Режим миграции: DB секреты не загружаются из settings")
        else:
            # Dev/Prod - название БД берем из Vault
            # USER и PASSWORD будут получены динамически через DatabaseManager
            try:
                from app.services.vault_client import vault_client

                db_secrets = vault_client.get_secret("database")
                self.NAME = db_secrets.get("CUSTODY_DB_NAME", self.NAME)
                logging.info(f"✅ Название БД получено из Vault ({stand}): {self.NAME}")
            except Exception as e:
                logging.error(
                    f"❌ Ошибка получения названия БД из Vault ({stand}): {e}"
                )
                raise

    @property
    def connection_string(self) -> str:
        """
        URL подключения к БД.
        Для local: статичный URL с credentials из env.
        Для dev/prod: используется только для local, в prod URL генерируется в DatabaseManager.
        """
        return f"postgresql+asyncpg://{self.USER}:{self.PASSWORD}@{self.HOST}:{self.PORT}/{self.NAME}"


class FireblocksSettings(BaseModel):
    """Fireblocks API settings."""

    SANDBOX_URL: str = "https://sandbox-api.fireblocks.io"
    PRODUCTION_URL: str = "https://api.fireblocks.io"
    SANDBOX: bool = getenv("FIREBLOCKS_SANDBOX", "true").lower() == "true"


class RabbitMQSettings(BaseModel):
    """Настройки для RabbitMQ"""

    HOST: str = getenv("RABBIT_HOST", "localhost")
    PORT: int = int(getenv("RABBIT_PORT", "5672"))
    USER: str = getenv("RABBIT_USER", "guest")
    PASSWORD: str = getenv("RABBIT_PASSWORD", "guest")
    EXCHANGE: str = getenv("RABBITMQ_EXCHANGE", "custody_events")

    # в колбек
    CALLBACK_QUEUE: str = "orders.custody.queue"
    CALLBACK_EXCHANGE: str = "orders.custody.exchange"
    
    # Transfer pipeline
    TRANSFER_EXCHANGE: str = "transfer.exchange"

    def __init__(self, **data):
        super().__init__(**data)
        stand = getenv("STAND", "local")
        migrate_stand = getenv("MIGRATE_STAND")

        if stand == "local":
            # Local dev - читаем из env
            logging.warning(
                "⚠️ RabbitMQ: используем локальные переменные для подключения"
            )
        elif migrate_stand:
            # Режим миграции - не загружаем RabbitMQ секреты (нет доступа у runner-dev)
            logging.info("⚠️ Режим миграции: RabbitMQ секреты не загружаются")
        else:
            # Dev/Prod - читаем из Vault (общий секрет для окружения)
            # secret path: <env>/rabbitmq
            try:
                from app.services.vault_client import vault_client

                rabbitmq_secrets = vault_client.get_secret("rabbitmq")
                self.USER = rabbitmq_secrets["RABBIT_USER"]
                self.PASSWORD = rabbitmq_secrets["RABBIT_PASSWORD"]
                logging.info(
                    f"✅ Используем динамические credentials из Vault ({stand}) для RabbitMQ"
                )
            except Exception as e:
                logging.error(
                    f"❌ Использование динамических credentials из Vault ({stand}) для RabbitMQ: {e}"
                )
                raise

    @property
    def connection_string(self) -> str:
        """Get RabbitMQ connection string."""
        return f"amqp://{self.USER}:{self.PASSWORD}@{self.HOST}:{self.PORT}/"


class RedisSettings(BaseModel):
    """Redis settings for distributed locks and caching."""

    HOST: str = getenv("REDIS_HOST", "localhost")
    PORT: int = 6379  # Default, will be set in __init__
    PASSWORD: str = getenv("REDIS_PASSWORD", "")
    DB: int = int(getenv("REDIS_DB", "0"))

    def __init__(self, **data):
        super().__init__(**data)
        stand = getenv("STAND", "local")
        migrate_stand = getenv("MIGRATE_STAND")

        # Parse port carefully - K8s may set REDIS_PORT to service URL like "tcp://10.x.x.x:6379"
        port_env = getenv("CUSTODY_REDIS_PORT", getenv("REDIS_PORT", "6379"))
        if port_env.startswith("tcp://"):
            self.PORT = int(port_env.split(":")[-1])
        else:
            self.PORT = int(port_env)

        if stand == "local":
            logging.warning("⚠️ Redis: используем локальные переменные для подключения")
        elif migrate_stand:
            logging.info("⚠️ Режим миграции: Redis секреты не загружаются")
        else:
            # Dev/Prod - читаем из Vault
            try:
                from app.services.vault_client import vault_client

                redis_secrets = vault_client.get_secret("redis")
                self.HOST = redis_secrets.get("host", self.HOST)
                self.PORT = int(redis_secrets.get("port", self.PORT))
                self.PASSWORD = redis_secrets.get("password", self.PASSWORD)
                logging.info(f"✅ Redis: секреты загружены из Vault ({stand})")
            except Exception as e:
                logging.warning(f"⚠️ Redis секреты не найдены в Vault ({stand}): {e}")
                # Redis optional - continue without it

    @property
    def url(self) -> str:
        """Redis connection URL."""
        if self.PASSWORD:
            return f"redis://:{self.PASSWORD}@{self.HOST}:{self.PORT}/{self.DB}"
        return f"redis://{self.HOST}:{self.PORT}/{self.DB}"


class Settings(BaseSettings):
    """Main settings class."""

    vault: VaultSettings = VaultSettings()
    app: AppSettings = AppSettings()
    database: DatabaseSettings = DatabaseSettings()
    fireblocks: FireblocksSettings = FireblocksSettings()
    rabbitmq: RabbitMQSettings = RabbitMQSettings()
    redis: RedisSettings = RedisSettings()


# Global settings instance
cfg = Settings()
