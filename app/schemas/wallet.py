import uuid
from pydantic import BaseModel, ConfigDict


class WalletWithVaultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    wallet_id: uuid.UUID
    vault_id: uuid.UUID
    asset_id: str
    blockchain: str
    currency: str
    network: str
    address: str
    legacy_address: str | None = None
    tag: str | None = None
