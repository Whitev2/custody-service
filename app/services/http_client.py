import aiohttp


class HTTPClient:
    session: aiohttp.ClientSession | None = None

    @classmethod
    def get_session(cls) -> aiohttp.ClientSession:
        if cls.session is None:
            raise RuntimeError("HTTP Client not initialized")
        return cls.session

    @classmethod
    def initialize(cls):
        if cls.session is None:
            # Use specific timeout or default settings if needed
            timeout = aiohttp.ClientTimeout(total=30)
            cls.session = aiohttp.ClientSession(timeout=timeout)

    @classmethod
    async def close(cls):
        if cls.session:
            await cls.session.close()
            cls.session = None


http_client = HTTPClient()
