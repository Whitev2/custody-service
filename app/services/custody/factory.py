from app.config import cfg, log
from app.services.custody.providers import (
    BaseProvider,
    FireblocksProvider,
    CustomProvider,
)


def create_provider(provider_name: str | None = None) -> BaseProvider:
    # None -> дефолт из конфига
    if provider_name is None:
        provider_name = getattr(cfg, "default_provider", "fireblocks")

    provider_name = provider_name.lower()

    if provider_name == "fireblocks":
        log.info("Using Fireblocks provider")
        return FireblocksProvider()
    elif provider_name == "custom":
        log.info("Using custom provider")
        return CustomProvider()
    else:
        raise ValueError(f"Unsupported provider: {provider_name}")


_provider: BaseProvider | None = None


def get_provider() -> BaseProvider:
    global _provider
    if _provider is None:
        _provider = create_provider()
    return _provider


def set_provider(provider: BaseProvider | None) -> None:
    # для тестов / кастомных провайдеров
    global _provider
    _provider = provider
    if provider:
        log.info(f"Provider set to: {provider.provider_name}")
    else:
        log.info("Provider reset to None")
