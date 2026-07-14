import os
import time
from logging.config import fileConfig

from sqlalchemy import pool, create_engine
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError

from alembic import context

from app.config import cfg, log
from app.models import *  # noqa

config = context.config


def get_db_url() -> str:
    # local - статичный URL из cfg, dev/prod - креды мигратора из Vault
    stand = os.getenv("STAND", "local")

    if stand == "local":
        return cfg.database.connection_string.replace("postgresql+asyncpg://", "postgresql+psycopg2://")

    try:
        from app.services.vault_client import vault_client


        db_secrets = vault_client.get_secret("database")
        migrator_user = db_secrets.get("MIGRATOR_USER")
        migrator_password = db_secrets.get("MIGRATOR_PASSWORD")
        db_name = db_secrets.get("CUSTODY_DB_NAME")

        if not migrator_user or not migrator_password:
            raise RuntimeError(
                "MIGRATOR_USER or MIGRATOR_PASSWORD not found in Vault (secret: <env>/database)"
            )

        if not db_name:
            raise RuntimeError(
                "CUSTODY_DB_NAME not found in Vault (secret: <env>/database)"
            )

        # HOST/PORT из env (CI устанавливает их в скрипте миграции)
        db_host = os.getenv("CUSTODY_DB_HOST", cfg.database.HOST)
        db_port = os.getenv("CUSTODY_DB_PORT", str(cfg.database.PORT))

        db_url = (
            f"postgresql+psycopg2://{migrator_user}:{migrator_password}"
            f"@{db_host}:{db_port}/{db_name}"
        )
        log.info(f"✅ Alembic: migrator credentials from Vault (host={db_host}, db={db_name})")
        return db_url
    except Exception as e:
        log.error(f"❌ Alembic: ошибка получения migrator credentials из Vault: {e}")
        raise


db_url = get_db_url()
config.set_main_option("sqlalchemy.url", db_url)


if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # синхронный psycopg2 вместо asyncpg + use_native_hstore=False -
    # иначе Odyssey в transaction pooling mode ломается на prepared statements / hstore OIDs
    connectable = create_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=pool.NullPool,
        connect_args={
            "options": "-c timezone=UTC"
        },
        use_native_hstore=False,
    )

    # retry для временных сбоев Odyssey
    max_retries = 3
    retry_delay = 2  # секунды

    for attempt in range(max_retries):
        try:
            with connectable.connect() as connection:
                context.configure(
                    connection=connection,
                    target_metadata=target_metadata
                )

                with context.begin_transaction():
                    context.run_migrations()
            break
        except OperationalError as e:
            error_msg = str(e)
            retryable_errors = [
                "failed to make auth query",
                "server closed the connection",
                "connection refused",
                "could not connect to server",
            ]
            is_retryable = any(err in error_msg.lower() for err in retryable_errors)

            if is_retryable and attempt < max_retries - 1:
                log.warning(
                    f"⚠️ Odyssey connection error (attempt {attempt + 1}/{max_retries}): "
                    f"{error_msg[:100]}... Retrying in {retry_delay}s..."
                )
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                raise


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
