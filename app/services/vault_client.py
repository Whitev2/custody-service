"""Vault Client для чтения секретов через Kubernetes Auth"""

import asyncio
import logging
import os
import threading
import time

import hvac

from cachetools import TTLCache

log = logging.getLogger("app")


class VaultClient:
    """
    Vault client для чтения секретов через Kubernetes Auth

    Структура секретов (mount и базовый путь настраиваются через env):
    - {VAULT_KV_MOUNT}/{VAULT_SECRET_BASE}/{env}/redis - общий Redis для окружения
    - {VAULT_KV_MOUNT}/{VAULT_SECRET_BASE}/{env}/rabbitmq - общий RabbitMQ для окружения
    - {VAULT_KV_MOUNT}/{VAULT_SECRET_BASE}/{env}/database - централизованные названия БД
    - {VAULT_KV_MOUNT}/{VAULT_SECRET_BASE}/{env}/custody - секреты custody

    Пример использования:
        vault_client = VaultClient()
        db_secrets = vault_client.get_secret("database")
        redis_secrets = vault_client.get_secret("redis")
    """

    def __init__(self):
        """
        Инициализация VaultClient.

        Кэш секретов: TTL=3600s (1 час), max 100 записей
        """
        self.client: hvac.Client | None = None
        self._environment: str | None = None
        # KV v2 mount и базовый путь для секретов (генерик по умолчанию, настраивается через env)
        self._kv_mount: str = os.getenv("VAULT_KV_MOUNT", "kv")
        self._secret_base: str = os.getenv("VAULT_SECRET_BASE", "custody")
        # Vault auth role (Kubernetes auth), настраивается через env
        self._auth_mount: str = os.getenv("VAULT_AUTH_MOUNT", "kubernetes")
        # TTL кэш: секреты живут 1 час, максимум 100 записей
        self._secret_cache = TTLCache(maxsize=100, ttl=3600)

        # Отслеживание TTL токена Vault
        self._token_lease_duration: int | None = None
        self._token_expires_at: float | None = None
        self._token_renewable: bool | None = None
        self._token_accessor: str | None = None
        self._auth_lock = threading.Lock()

        # Фоновая задача обновления токена
        self._refresh_task: asyncio.Task | None = None
        self._shutdown_event: asyncio.Event | None = None

        self._initialize()

    def _initialize(self):
        """Инициализация Vault client"""
        vault_addr = os.getenv("VAULT_ADDR", "")
        stand = os.getenv("STAND", "local")

        # Для local не используем Vault
        if stand == "local":
            log.warning("⚠️ Vault: STAND=local, Vault client not initialized")
            return

        # Проверяем наличие VAULT_ADDR
        if not vault_addr:
            log.warning("⚠️ Vault: VAULT_ADDR not set, Vault client not initialized")
            return

        self._environment = stand  # dev или prod
        token_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"

        if self.client is None:
            self.client = hvac.Client(url=vault_addr)

        # Kubernetes Auth
        if os.path.exists(token_path):
            with open(token_path) as f:
                jwt = f.read()

            # Роль Vault настраивается через env.
            # По умолчанию используется роль вида "<VAULT_ROLE_PREFIX>-<env>".
            # Для миграций можно задать отдельную роль через VAULT_MIGRATE_ROLE.
            role_prefix = os.getenv("VAULT_ROLE_PREFIX", "custody")
            role = os.getenv("VAULT_ROLE", f"{role_prefix}-{self._environment}")

            migrate_stand = os.getenv("MIGRATE_STAND")
            migrate_role = os.getenv("VAULT_MIGRATE_ROLE")
            if migrate_stand and migrate_role:
                role = migrate_role

            try:
                response = self.client.auth.kubernetes.login(
                    role=role, jwt=jwt, mount_point=self._auth_mount
                )
                # Сохраняем информацию о TTL токена
                self._token_lease_duration = response["auth"]["lease_duration"]
                self._token_expires_at = time.time() + self._token_lease_duration
                self._token_renewable = response["auth"].get("renewable")
                self._token_accessor = response["auth"].get("accessor")

                log.info(f"✅ Vault: authenticated as {role}")
                log.info(
                    f"✅ Vault: token lease duration: {self._token_lease_duration}s "
                    f"({self._token_lease_duration // 60} мин)"
                )
            except Exception as e:
                log.error(f"❌ Vault: authentication failed: {e}")
                raise
        else:
            # Dev mode - без Kubernetes auth
            log.warning("⚠️ Vault: running without Kubernetes auth (local dev mode)")
            log.warning("⚠️ Vault: secrets will be read from environment variables")

    def reauthenticate(self):
        """Переавторизоваться в Vault (при истечении token). Потокобезопасно."""
        with self._auth_lock:
            log.info("🔄 Vault: re-authenticating...")
            self._secret_cache.clear()
            self._initialize()

    def get_token_accessor(self) -> str | None:
        return self._token_accessor

    def renew_token(self) -> bool:
        if not self.client:
            return False

        try:
            self.client.auth.token.renew_self()

            info = self.client.auth.token.lookup_self()
            ttl = info.get("data", {}).get("ttl")
            renewable = info.get("data", {}).get("renewable")
            accessor = info.get("data", {}).get("accessor")

            if isinstance(ttl, int):
                self._token_lease_duration = ttl
                self._token_expires_at = time.time() + ttl
            self._token_renewable = renewable
            if accessor:
                self._token_accessor = accessor

            return True
        except Exception as e:
            log.warning(f"⚠️ Vault: token renew failed: {e}")
            return False

    def get_token_info(self) -> dict:
        """Получить информацию о текущем токене Vault для мониторинга."""
        if not self.client or not self._token_expires_at:
            return {"status": "not_authenticated", "time_left_seconds": None}

        time_left = max(0, int(self._token_expires_at - time.time()))
        return {
            "status": "authenticated",
            "lease_duration": self._token_lease_duration,
            "renewable": self._token_renewable,
            "accessor": self._token_accessor,
            "time_left_seconds": time_left,
            "time_left_minutes": time_left // 60,
        }

    def is_token_expiring_soon(self, threshold_seconds: int = 300) -> bool:
        """
        Проверить, истекает ли токен Vault в ближайшее время.

        Args:
            threshold_seconds: Порог в секундах (по умолчанию 5 минут)

        Returns:
            True если токен истекает в течение threshold_seconds
        """
        if not self._token_expires_at:
            return True  # Нет информации о токене — считаем, что нужно обновить

        time_left = self._token_expires_at - time.time()
        return time_left < threshold_seconds

    async def start_background_refresh(
        self,
        check_interval: int = 300,
        refresh_threshold: int = 600,
    ):
        """
        Запустить фоновую задачу проактивного обновления токена Vault.

        Args:
            check_interval: Интервал проверки в секундах (по умолчанию 5 минут)
            refresh_threshold: Порог до истечения, при котором обновляем (по умолчанию 10 минут)
        """
        stand = os.getenv("STAND", "local")
        if stand == "local":
            log.info("⚠️ STAND=local, фоновое обновление Vault токена не требуется")
            return

        if self._refresh_task is not None:
            log.warning("⚠️ Фоновая задача обновления Vault токена уже запущена")
            return

        self._shutdown_event = asyncio.Event()
        self._refresh_task = asyncio.create_task(
            self._token_refresh_loop(check_interval, refresh_threshold),
            name="vault_token_refresh",
        )
        log.info(
            f"✅ Фоновая задача обновления Vault токена запущена "
            f"(проверка каждые {check_interval}s, обновление за {refresh_threshold}s до истечения)"
        )

    async def stop_background_refresh(self):
        """Остановить фоновую задачу обновления токена Vault."""
        if self._refresh_task is None:
            return

        log.info("🔄 Остановка фоновой задачи обновления Vault токена...")
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
        log.info("✅ Фоновая задача обновления Vault токена остановлена")

    async def _token_refresh_loop(self, check_interval: int, refresh_threshold: int):
        """
        Фоновая задача: проверяет TTL токена Vault и обновляет его,
        если до истечения осталось меньше refresh_threshold секунд.
        """
        log.info(
            f"🔄 Vault token refresh loop: проверка каждые {check_interval}s, "
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
                    # Если дошли сюда — shutdown_event установлен
                    break
                except asyncio.TimeoutError:
                    # Таймаут — это нормально, продолжаем проверку
                    pass

                # Проверяем и обновляем токен если нужно
                if self.is_token_expiring_soon(refresh_threshold):
                    token_info = self.get_token_info()
                    time_left = token_info.get("time_left_seconds", 0)
                    log.info(
                        f"🔄 Vault токен истекает через {time_left}s, обновляем..."
                    )

                    renewed = False
                    if self._token_renewable is not False:
                        renewed = self.renew_token()

                    if not renewed:
                        self.reauthenticate()

                    new_info = self.get_token_info()
                    log.info(
                        f"✅ Vault токен обновлён, новый TTL: "
                        f"{new_info.get('time_left_seconds', 0) // 60} мин"
                    )
                # Token is still valid, no action needed

            except asyncio.CancelledError:
                log.info("🔄 Vault token refresh loop отменён")
                break
            except Exception as e:
                log.error(f"❌ Ошибка в vault token refresh loop: {e}")
                # Продолжаем работу, не падаем

        log.info("🔄 Vault token refresh loop завершён")

    def get_secret(self, path: str) -> dict:
        """
        Получить секрет из KV Secrets (с TTL кэшированием)

        Кэш: TTL=3600s (1 час), автоматическое вытеснение устаревших записей

        Args:
            path: Путь относительно {VAULT_SECRET_BASE}/{env}/
                  Примеры (при VAULT_KV_MOUNT=kv, VAULT_SECRET_BASE=custody):
                  - "redis" → читает kv/custody/dev/redis
                  - "rabbitmq" → читает kv/custody/dev/rabbitmq
                  - "custody" → читает kv/custody/dev/custody
                  - "database" → читает kv/custody/dev/database

        Returns:
            dict: Секреты

        Example:
            # Общие секреты окружения
            redis_secrets = vault_client.get_secret("redis")
            # {'host': '<redis-host>', 'port': '6379', 'password': '...'}

            # Секреты custody
            custody_secrets = vault_client.get_secret("custody")
            # {'JWT_SECRET_KEY': '...', ...}
        """
        if not self.client:
            raise RuntimeError(
                "Vault client not initialized. Check STAND and VAULT_ADDR environment variables."
            )

        # Проверяем TTL кэш (автоматически удаляет устаревшие записи)
        if path in self._secret_cache:
            log.debug(f"🔄 Vault: using cached secret for '{path}' (TTL cache)")
            return self._secret_cache[path]

        full_path = f"{self._secret_base}/{self._environment}/{path}"

        try:
            response = self.client.secrets.kv.v2.read_secret_version(
                path=full_path, mount_point=self._kv_mount
            )
            secret_data = response["data"]["data"]

            # Кэшируем секрет с TTL=3600s
            self._secret_cache[path] = secret_data

            log.info(f"✅ Vault: read secret from {full_path} (cached for 1h)")
            return secret_data
        except Exception as e:
            log.error(f"❌ Vault: failed to read secret from {full_path}: {e}")
            raise


# Singleton
vault_client = VaultClient()
