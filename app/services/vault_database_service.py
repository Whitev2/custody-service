import logging
import os
import time

import dateutil.parser
import hvac

from app.services.vault_client import vault_client

log = logging.getLogger("app")


class VaultDatabaseService:
    """
    Сервис для работы с Vault Database Secrets Engine (static roles).
    Получает статические credentials с ротацией пароля для PostgreSQL.
    """

    def __init__(self, vc: hvac.Client, role_name: str):
        self.client = vc
        self.role_name = role_name
        self._credentials = None
        self._last_rotation_time = None
        self._rotation_period = None
        self._vault_token_accessor = None

    def get_credentials(self, force_refresh: bool = False) -> dict[str, str]:
        """
        Получить статические credentials из Vault.

        Args:
            force_refresh: Принудительно запросить обновленные credentials

        Returns:
            dict: {'username': '<static_role_name>', 'password': '<rotated_password>'}
        """
        current_accessor = None
        try:
            current_accessor = vault_client.get_token_accessor()
        except Exception:
            current_accessor = None

        if (
            self._credentials
            and self._vault_token_accessor
            and current_accessor
            and current_accessor != self._vault_token_accessor
        ):
            self._credentials = None
            self._last_rotation_time = None
            self._rotation_period = None
            self._vault_token_accessor = None

        if force_refresh or not self._credentials:
            log.info(f"Запрос credentials из Vault для роли: {self.role_name}")
            self._fetch_credentials()
        return self._credentials.copy()

    def _fetch_credentials(self, retry_auth: bool = True):
        """Получить статические credentials из Vault Database Secrets Engine"""
        try:
            # Используем static-creds вместо creds для статических ролей
            response = self.client.read(f"database/static-creds/{self.role_name}")

            self._credentials = {
                "username": response["data"]["username"],
                "password": response["data"]["password"],
            }
            last_rotation = response["data"].get("last_vault_rotation")
            if last_rotation:
                # Vault возвращает время в формате ISO8601, конвертируем в timestamp
                dt = dateutil.parser.isoparse(last_rotation)
                self._last_rotation_time = dt.timestamp()
            else:
                self._last_rotation_time = time.time()

            rotation_period = response["data"].get("rotation_period", 3600)
            self._rotation_period = int(rotation_period) if rotation_period else 3600

            try:
                self._vault_token_accessor = vault_client.get_token_accessor()
            except Exception:
                self._vault_token_accessor = None

            log.info(
                f"✅ Получены статические credentials: user={self._credentials['username']}, "
                f"rotation_period={self._rotation_period}s"
            )

        except Exception as e:
            error_msg = str(e).lower()
            # При ошибке token (истёк/отозван) пробуем переавторизоваться
            if retry_auth and (
                "permission denied" in error_msg or "invalid token" in error_msg
            ):
                log.warning("⚠️ Vault token error, attempting re-authentication...")
                try:
                    vault_client.reauthenticate()
                    self.client = vault_client.client
                    self._credentials = None
                    self._last_rotation_time = None
                    self._rotation_period = None
                    self._vault_token_accessor = None
                    self._fetch_credentials(retry_auth=False)
                    return
                except Exception as reauth_error:
                    log.error(f"❌ Re-authentication failed: {reauth_error}")

            log.error(f"❌ Ошибка получения credentials из Vault: {e}")
            raise

    def get_credentials_info(self) -> dict:
        """Получить информацию о текущих статических credentials для мониторинга"""
        if not self._credentials:
            return {"status": "no_credentials"}

        time_since_rotation = None
        if self._last_rotation_time:
            time_since_rotation = int(time.time() - self._last_rotation_time)

        return {
            "status": "active",
            "username": self._credentials.get("username"),
            "last_rotation_time": self._last_rotation_time,
            "rotation_period": self._rotation_period,
            "time_since_rotation_seconds": time_since_rotation,
        }

    def should_refresh_credentials(self, threshold_seconds: int = 300) -> bool:
        """
        Проверить, нужно ли обновить credentials (для проактивного обновления).

        Args:
            threshold_seconds: Порог в секундах до ротации (по умолчанию 5 минут)

        Returns:
            True если приближается время ротации или credentials устарели
        """
        if not self._last_rotation_time or not self._rotation_period:
            return True

        time_since_rotation = time.time() - self._last_rotation_time
        time_until_rotation = self._rotation_period - time_since_rotation
        return time_until_rotation < threshold_seconds


def create_vault_database_service() -> VaultDatabaseService | None:
    """Создать VaultDatabaseService (None для STAND=local)"""
    stand = os.getenv("STAND", "local")

    if stand == "local":
        log.info("⚠️ STAND=local, VaultDatabaseService не инициализирован")
        return None

    role_name = f"custody-{stand}-role"

    try:
        if not vault_client.client:
            raise RuntimeError("Vault client не инициализирован")

        service = VaultDatabaseService(vc=vault_client.client, role_name=role_name)

        log.info(f"✅ VaultDatabaseService инициализирован для роли: {role_name}")
        return service

    except Exception as e:
        log.error(f"❌ Ошибка инициализации VaultDatabaseService: {e}")
        raise
