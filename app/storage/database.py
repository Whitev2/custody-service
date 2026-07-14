"""Database connection and session management with Vault credentials rotation."""

import asyncio
import os
import socket
import uuid

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.exc import DisconnectionError, OperationalError, ProgrammingError
from asyncpg.exceptions import (
    ConnectionDoesNotExistError,
    InsufficientPrivilegeError,
    InvalidAuthorizationSpecificationError,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from urllib.parse import urlsplit

from app.config import cfg, log
from app.services.vault_database_service import (
    VaultDatabaseService,
    create_vault_database_service,
)


class DatabaseManager:
    """
    Менеджер подключений к БД со статическими credentials из Vault.
    Реинициализация при ротации пароля, ping через SELECT 1, retry, защита от race.
    """

    def __init__(self):
        self._async_engine: AsyncEngine | None = None
        self._async_session: async_sessionmaker | None = None
        self._current_db_url: str | None = None
        self._current_db_username: str | None = None
        self._vault_db_service: VaultDatabaseService | None = None
        self._is_local: bool = os.getenv("STAND", "local") == "local"
        self._initialized: bool = False
        self._reinit_lock: asyncio.Lock = asyncio.Lock()
        self._refresh_task: asyncio.Task | None = None
        self._shutdown_event: asyncio.Event = asyncio.Event()

    @staticmethod
    def _get_pool_settings() -> dict:
        stand = os.getenv("STAND", "local")
        is_prod = stand == "prod"

        pool_size = 10 if is_prod else 5
        max_overflow = 10 if is_prod else 5
        pool_timeout = 30
        pool_recycle = 150  # 150 sec, меньше чем pool_ttl=180 в Odyssey

        return {
            "pool_size": pool_size,
            "max_overflow": max_overflow,
            "pool_timeout": pool_timeout,
            "pool_recycle": pool_recycle,
        }

    async def initialize(self, db_url: str = None):
        """Инициализация / переинициализация подключения."""
        if db_url is None:
            db_url = self._get_db_url()

        # Если URL не изменился и engine существует, ничего не делаем
        if db_url == self._current_db_url and self._async_engine is not None:
            return

        # Закрываем старое подключение
        if self._async_engine:
            log.info("Закрытие старого подключения...")
            await self._async_engine.dispose()

        self._current_db_url = db_url
        self._current_db_username = self._extract_username_from_db_url(db_url)
        pool_settings = self._get_pool_settings()
        self._async_engine = create_async_engine(
            url=db_url,
            echo=False,
            pool_pre_ping=True,
            pool_use_lifo=True,
            **pool_settings,
            connect_args={
                "prepared_statement_name_func": lambda: f"__asyncpg_{uuid.uuid4()}__",
                "statement_cache_size": 0,
                "prepared_statement_cache_size": 0,
                "timeout": 10,  # Connection timeout (включая DNS resolution)
                "command_timeout": 30,  # Таймаут выполнения команды
                "server_settings": {
                    "timezone": "UTC",
                    "jit": "off",  # Отключить JIT для стабильности
                },
            },
        )

        self._async_session = async_sessionmaker(
            bind=self._async_engine,
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,
            class_=AsyncSession,
        )

        self._initialized = True
        log.info("✅ Database engine инициализирован")
        if self._current_db_username:
            log.info(f"✅ Database user: {self._current_db_username}")

    def _get_db_url(self, force_refresh: bool = False) -> str:
        """
        Получить URL подключения к БД.
        Для local - из cfg, для dev/prod - со статическими credentials из Vault.
        """
        if self._is_local:
            return cfg.database.connection_string

        # Dev/Prod: используем статические credentials из Vault
        if not self._vault_db_service:
            self._vault_db_service = create_vault_database_service()

        if self._vault_db_service:
            # Получаем credentials (только при force_refresh или первом запросе)
            creds = self._vault_db_service.get_credentials(force_refresh=force_refresh)
            self._current_db_username = creds.get("username")
            db_url = (
                f"postgresql+asyncpg://{creds['username']}:{creds['password']}"
                f"@{cfg.database.HOST}:{cfg.database.PORT}/{cfg.database.NAME}"
            )
            return db_url
        else:
            log.warning("⚠️ VaultDatabaseService недоступен")
            raise RuntimeError("VaultDatabaseService недоступен")

    @staticmethod
    def _extract_username_from_db_url(db_url: str) -> str | None:
        try:
            parts = urlsplit(db_url)
            return parts.username
        except Exception:
            return None

    def _should_force_refresh_from_vault(self, e: Exception) -> bool:
        if self._is_local:
            return False

        error_msg = str(e).lower()

        orig = getattr(e, "orig", None)

        if isinstance(e, InsufficientPrivilegeError) or isinstance(
            orig, InsufficientPrivilegeError
        ):
            return True

        if "permission denied" in error_msg:
            return True

        if isinstance(e, InvalidAuthorizationSpecificationError):
            return True

        return (
            "failed to make auth query" in error_msg
            or "password authentication failed" in error_msg
            or "invalid password" in error_msg
            or "invalid authorization specification" in error_msg
        )

    async def get_db(self) -> AsyncGenerator[AsyncSession, None]:
        """
        Dependency для FastAPI с автоматическим обновлением подключения.
        При ошибке SELECT 1 запрашивает новые credentials из Vault.
        """
        # Автоматическая инициализация если не была вызвана
        if self._async_session is None:
            await self.initialize()

        max_retries = 3
        session = None

        for attempt in range(max_retries):
            try:
                # Создаем сессию
                session = self._async_session()

                # Тестируем соединение
                try:
                    await session.execute(text("SELECT 1"))
                except (ConnectionDoesNotExistError, DisconnectionError, OperationalError) as ping_error:
                    # Соединение из пула было закрыто, закрываем сессию и повторяем
                    if session:
                        await session.close()
                        session = None
                    raise ping_error
                break

            except (
                DisconnectionError,
                OperationalError,
                ProgrammingError,  # Includes InsufficientPrivilegeError wrapped by SQLAlchemy
                ConnectionDoesNotExistError,
                InvalidAuthorizationSpecificationError,  # Odyssey auth query failed
                InsufficientPrivilegeError,  # Permission denied - credentials may be stale
                socket.gaierror,
                OSError,
            ) as e:
                error_type = type(e).__name__
                log.warning(
                    f"DB connection error [{error_type}] (attempt {attempt + 1}/{max_retries}) "
                    f"(db_user={self._current_db_username}): {e}"
                )

                if session:
                    await session.close()
                    session = None

                # Для dev/prod: при ошибке подключения или прав запрашиваем новые credentials
                if (
                    not self._is_local
                    and attempt < max_retries - 1
                    and self._should_force_refresh_from_vault(e)
                ):
                    log.info("🔄 Запрос новых credentials из Vault...")
                    try:
                        new_url = self._get_db_url(force_refresh=True)
                        log.info(
                            "Новые credentials получены, переинициализация подключения..."
                        )
                        await self.initialize(new_url)
                    except Exception as vault_error:
                        log.error(
                            f"Ошибка получения новых credentials из Vault: {vault_error}"
                        )

                if attempt == max_retries - 1:
                    log.error(f"Max retries reached after {error_type}")
                    raise

                backoff = (2**attempt) + (0.1 * attempt)
                log.info(f"Retrying DB connection in {backoff:.1f}s...")
                await asyncio.sleep(backoff)

        # После успешного создания сессии - yield без retry
        try:
            yield session
        finally:
            if session:
                await session.close()

    @asynccontextmanager
    async def get_db_local(self) -> AsyncGenerator[AsyncSession, None]:
        """
        Локальная сессия для использования в сервисах и фоновых задачах.
        Требует явного управления транзакциями (commit/rollback).
        Включает retry логику для временных сбоев Odyssey.
        """
        # Автоматическая инициализация если не была вызвана
        if self._async_session is None:
            await self.initialize()

        max_retries = 3
        session = None

        for attempt in range(max_retries):
            try:
                session = self._async_session()
                # Тестируем соединение перед использованием
                try:
                    await session.execute(text("SELECT 1"))
                except (ConnectionDoesNotExistError, DisconnectionError, OperationalError) as ping_error:
                    # Соединение из пула было закрыто, закрываем сессию и повторяем
                    if session:
                        await session.close()
                        session = None
                    raise ping_error
                break
            except (
                DisconnectionError,
                OperationalError,
                ProgrammingError,  # Includes InsufficientPrivilegeError wrapped by SQLAlchemy
                ConnectionDoesNotExistError,
                InvalidAuthorizationSpecificationError,  # Odyssey auth query failed
                InsufficientPrivilegeError,  # Permission denied - credentials may be stale
                socket.gaierror,
                OSError,
            ) as e:
                error_type = type(e).__name__
                log.warning(
                    f"DB connection error in get_db_local [{error_type}] "
                    f"(attempt {attempt + 1}/{max_retries}) "
                    f"(db_user={self._current_db_username}): {e}"
                )
                if session:
                    await session.close()
                    session = None

                # Для dev/prod: при ошибке подключения или прав запрашиваем новые credentials
                if (
                    not self._is_local
                    and attempt < max_retries - 1
                    and self._should_force_refresh_from_vault(e)
                ):
                    log.info("🔄 Запрос новых credentials из Vault (get_db_local)...")
                    try:
                        new_url = self._get_db_url(force_refresh=True)
                        log.info(
                            "Новые credentials получены, переинициализация подключения..."
                        )
                        await self.initialize(new_url)
                    except Exception as vault_error:
                        log.error(
                            f"Ошибка получения новых credentials из Vault: {vault_error}"
                        )

                if attempt == max_retries - 1:
                    log.error(f"Max retries reached in get_db_local after {error_type}")
                    raise

                backoff = (2**attempt) + (0.1 * attempt)
                log.info(f"Retrying DB connection in {backoff:.1f}s...")
                await asyncio.sleep(backoff)

        try:
            yield session
        except Exception as e:
            if session:
                await session.rollback()

            # Для dev/prod: при ошибках авторизации/прав внутри контекста
            # пробуем форсировать обновление credentials из Vault, чтобы
            # следующая попытка использовала новый роль/пользователя.
            should_refresh = False

            if not self._is_local:
                should_refresh = self._should_force_refresh_from_vault(e)

                if should_refresh:
                    log.info(
                        "Detected possible DB auth issue in get_db_local "
                        "(inside context), forcing refresh from Vault..."
                    )
                    try:
                        new_url = self._get_db_url(force_refresh=True)
                        log.info(
                            "Новые credentials получены после ошибки в get_db_local, "
                            "переинициализация подключения..."
                        )
                        await self.initialize(new_url)
                    except Exception as vault_error:
                        log.error(
                            "Ошибка обновления credentials из Vault после "
                            f"ошибки в get_db_local: {vault_error}"
                        )

            # Логируем саму ошибку: для ожидаемых кейсов устаревших прав как warning,
            # для прочих - как error.
            if should_refresh:
                log.warning(
                    f"Error in get_db_local (db auth issue, будет refresh): {e}"
                )
            else:
                log.error(f"Error in get_db_local: {e}")

            raise
        finally:
            if session:
                await session.close()

    @property
    def initialized(self) -> bool:
        """Проверка инициализации подключения к БД"""
        return self._initialized

    async def start_background_refresh(self):
        """
        Запустить фоновую задачу обновления credentials.
        Вызывать при старте приложения.
        """
        if self._is_local:
            log.info("⚠️ STAND=local, фоновое обновление credentials не требуется")
            return

        if self._refresh_task is not None:
            log.warning("⚠️ Фоновая задача обновления credentials уже запущена")
            return

        self._shutdown_event.clear()
        self._refresh_task = asyncio.create_task(
            self._credentials_refresh_loop(),
            name="db_credentials_refresh",
        )
        log.info("✅ Фоновая задача обновления DB credentials запущена")

    async def stop_background_refresh(self):
        """
        Остановить фоновую задачу обновления credentials.
        Вызывать при остановке приложения.
        """
        if self._refresh_task is None:
            return

        log.info("🔄 Остановка фоновой задачи обновления credentials...")
        self._shutdown_event.set()

        try:
            await asyncio.wait_for(self._refresh_task, timeout=5.0)
        except asyncio.TimeoutError:
            log.warning("⚠️ Фоновая задача не завершилась вовремя, отменяем...")
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass

        self._refresh_task = None
        log.info("✅ Фоновая задача обновления credentials остановлена")

    async def _credentials_refresh_loop(self):
        """
        Фоновая задача: проверяет TTL credentials и обновляет их,
        если до истечения осталось меньше порога.
        """
        check_interval = cfg.vault.DB_CREDENTIALS_CHECK_INTERVAL
        refresh_threshold = cfg.vault.DB_CREDENTIALS_REFRESH_THRESHOLD

        log.info(
            f"🔄 DB Credentials refresh loop: проверка каждые {check_interval}s, "
            f"обновление за {refresh_threshold}s до истечения"
        )

        while not self._shutdown_event.is_set():
            try:
                # Ждём интервал или сигнал остановки
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=check_interval,
                    )
                    # Если дошли сюда - значит shutdown_event установлен
                    break
                except asyncio.TimeoutError:
                    # Таймаут - это нормально, продолжаем проверку
                    pass

                await self._check_and_refresh_credentials()

            except asyncio.CancelledError:
                log.info("🔄 DB Credentials refresh loop отменён")
                break
            except Exception as e:
                log.error(f"❌ Ошибка в DB credentials refresh loop: {e}")
                # Продолжаем работу, не падаем

        log.info("🔄 DB Credentials refresh loop завершён")

    async def _check_and_refresh_credentials(self):
        """
        Проверить статус credentials и обновить если приближается ротация.
        """
        if not self._vault_db_service:
            return

        refresh_threshold = cfg.vault.DB_CREDENTIALS_REFRESH_THRESHOLD

        if self._vault_db_service.should_refresh_credentials(refresh_threshold):
            creds_info = self._vault_db_service.get_credentials_info()
            time_since = creds_info.get("time_since_rotation_seconds", 0)
            log.info(
                f"🔄 DB credentials приближаются к ротации (прошло {time_since}s), обновляем..."
            )
            async with self._reinit_lock:
                # Double-check под локом
                if self._vault_db_service.should_refresh_credentials(refresh_threshold):
                    try:
                        new_url = self._get_db_url(force_refresh=True)
                        await self.initialize(new_url)
                        new_creds_info = self._vault_db_service.get_credentials_info()
                        rotation_period = new_creds_info.get("rotation_period", 0)
                        log.info(
                            f"✅ DB credentials обновлены, rotation_period: "
                            f"{rotation_period // 60} мин"
                        )
                    except Exception as e:
                        log.error(f"❌ Ошибка обновления credentials: {e}")

    async def close_db_connect(self):
        """Закрыть соединение"""
        self._initialized = False
        if self._async_engine:
            await self._async_engine.dispose()


# Глобальный экземпляр менеджера БД
db_manager = DatabaseManager()


# Инициализация при старте приложения
async def init_db():
    """Инициализировать подключение к БД при старте приложения"""
    await db_manager.initialize()
    log.info("✅ Database initialized")


async def close_db():
    """Закрыть подключение к БД"""
    await db_manager.close_db_connect()
    log.info("✅ Database connections closed")


# Обертки для обратной совместимости
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency для FastAPI Depends"""
    async for session in db_manager.get_db():
        yield session


@asynccontextmanager
async def get_db_local() -> AsyncGenerator[AsyncSession, None]:
    """Локальная сессия для сервисов и фоновых задач"""
    async with db_manager.get_db_local() as session:
        yield session
