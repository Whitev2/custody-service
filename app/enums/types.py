from enum import Enum


class VaultTypeEnum(str, Enum):
    """Тип vault'а для treasury management."""

    HOT = "hot"              # Для мгновенных выводов пользователей
    WARM = "warm"            # Промежуточный буфер, пополняет HOT
    COLD = "cold"            # Долгосрочное хранение, ручные переводы
    REGULAR = "regular"      # Обычные vault'ы (депозиты, пользователи, etc)
    OPERATIONAL = "operational"  # Для оплаты комиссий и газа
