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

    key_file = getenv("PRIVATE_KEY_FILE", "")
    if key_file and os.path.exists(key_file):
        try:
            with open(key_file, "r") as f:
                return f.read().strip()
        except Exception as e:
            logging.warning(f"Failed to load private key from file {key_file}: {e}")

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

    key = getenv("PRIVATE_KEY", "")
    return key.replace("\\n", "\n") if key else None


class VaultSettings(BaseModel):
    """Настройки Vault credentials refresh"""

    TOKEN_CHECK_INTERVAL: int = 5 * 60  # 5 минут
    TOKEN_REFRESH_THRESHOLD: int = 10 * 60  # 10 минут
    DB_CREDENTIALS_CHECK_INTERVAL: int = 5 * 60  # 5 минут
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
            # local - читаем из файла или env
            self.API_KEY = getenv("API_KEY", "")
            self.PRIVATE_KEY = load_private_key_from_file() or getenv("PRIVATE_KEY", "")
        elif migrate_stand:
            logging.info("✅ Custody migration mode: skipping custody secrets")
        else:
            # dev/prod - читаем из Vault
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
        # dev/local → testnet, остальное → mainnet
        return self.STAND in ("dev", "local")


class DatabaseSettings(BaseModel):
    # local: статичные creds из env; dev/prod: динамические из Vault (в DatabaseManager).

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
            # миграция - имя БД из env (alembic/env.py сам достанет из Vault)
            logging.info("⚠️ Режим миграции: DB секреты не загружаются из settings")
        else:
            # dev/prod - имя БД из Vault; USER/PASSWORD динамически через DatabaseManager
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
        # только для local; в prod URL генерируется в DatabaseManager
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
            # миграция - RabbitMQ секреты не грузим (нет доступа у runner-dev)
            logging.info("⚠️ Режим миграции: RabbitMQ секреты не загружаются")
        else:
            # dev/prod - читаем из Vault (общий секрет на окружение)
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
            # dev/prod - читаем из Vault
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
        if self.PASSWORD:
            return f"redis://:{self.PASSWORD}@{self.HOST}:{self.PORT}/{self.DB}"
        return f"redis://{self.HOST}:{self.PORT}/{self.DB}"


class Settings(BaseSettings):
    vault: VaultSettings = VaultSettings()
    app: AppSettings = AppSettings()
    database: DatabaseSettings = DatabaseSettings()
    fireblocks: FireblocksSettings = FireblocksSettings()
    rabbitmq: RabbitMQSettings = RabbitMQSettings()
    redis: RedisSettings = RedisSettings()


cfg = Settings()
