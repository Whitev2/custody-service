# Custody Service

![Python](https://img.shields.io/badge/Python-3776AB?style=flat&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat&logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?style=flat&logo=postgresql&logoColor=white)
![RabbitMQ](https://img.shields.io/badge/RabbitMQ-FF6600?style=flat&logo=rabbitmq&logoColor=white)
![Vault](https://img.shields.io/badge/Vault-000000?style=flat&logo=vault&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?style=flat&logo=docker&logoColor=white)

A cryptocurrency **custody microservice** built on the Fireblocks API — manage vaults and wallets, resolve provider-agnostic assets, execute transfers, whitelist addresses, and process Fireblocks webhooks, publishing ledger events over a message broker. Secrets are sourced from HashiCorp Vault, and the asset model is provider-agnostic so additional custody backends can be added without schema changes.

> **About.** Built with my team at the fintech company I led as CEO; this is a sanitized, standalone extraction of the custody service, shared as a portfolio reference. All real credentials and provider keys have been removed — supply your own via environment variables / Vault.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CUSTODY SERVICE                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                   │
│  │   Backend    │───>│   Custody    │───>│  Fireblocks  │                   │
│  │   Service    │    │   Service    │    │     API      │                   │
│  └──────────────┘    └──────────────┘    └──────────────┘                   │
│         │                   │                   │                            │
│         │                   ▼                   │                            │
│         │           ┌──────────────┐            │                            │
│         │           │  PostgreSQL  │            │                            │
│         │           │   Database   │            │                            │
│         │           └──────────────┘            │                            │
│         │                   │                   │                            │
│         ▼                   ▼                   ▼                            │
│  ┌─────────────────────────────────────────────────────────────────┐        │
│  │                     CANONICAL ASSET MODEL                        │        │
│  │  ┌─────────────────────────────────────────────────────────┐    │        │
│  │  │  AssetModel (provider-agnostic)                         │    │        │
│  │  │  - symbol: USDT, ETH, TRX                               │    │        │
│  │  │  - blockchain: ETHEREUM, TRON, BITCOIN                  │    │        │
│  │  │  - contract_address: 0x... (tokens) / NULL (native)     │    │        │
│  │  │  - testnet: SEPOLIA, SHASTA / NULL (mainnet)            │    │        │
│  │  └─────────────────────────────────────────────────────────┘    │        │
│  │                            │                                     │        │
│  │                            ▼                                     │        │
│  │  ┌─────────────────────────────────────────────────────────┐    │        │
│  │  │  FireblocksAssetResolver (runtime)                      │    │        │
│  │  │  - Resolves Fireblocks ID by contract_address           │    │        │
│  │  │  - Resolves Fireblocks ID by blockchain (native)        │    │        │
│  │  │  - Caches results                                       │    │        │
│  │  └─────────────────────────────────────────────────────────┘    │        │
│  │                            │                                     │        │
│  │                            ▼                                     │        │
│  │  ┌─────────────────────────────────────────────────────────┐    │        │
│  │  │  Fireblocks Asset ID: USDT_ETH, ETH, TRX_TEST           │    │        │
│  │  └─────────────────────────────────────────────────────────┘    │        │
│  └─────────────────────────────────────────────────────────────────┘        │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Key Concept: Provider-Agnostic Assets

**The Fireblocks ID is NOT stored in the database.** Instead:

1. `AssetModel` holds **canonical** data: `symbol`, `blockchain`, `contract_address`
2. When calling the Fireblocks API, the ID is resolved **dynamically** via `FireblocksAssetResolver`
3. This makes it easy to add other custody providers in the future

### How Resolution Works:

```
┌─────────────────────────────────────────────────────────────────┐
│                  FIREBLOCKS ID RESOLUTION                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  For TOKENS (ERC20, TRC20, etc.):                               │
│  ┌──────────────────────┐     ┌──────────────────────┐          │
│  │  contract_address    │────>│  Fireblocks API      │          │
│  │  0xdAC17F958D2ee...  │     │  find by contract    │          │
│  └──────────────────────┘     └──────────────────────┘          │
│                                        │                         │
│                                        ▼                         │
│                               ┌──────────────────────┐          │
│                               │  USDT_ETH            │          │
│                               └──────────────────────┘          │
│                                                                  │
│  For NATIVE (ETH, BTC, TRX):                                    │
│  ┌──────────────────────┐     ┌──────────────────────┐          │
│  │  blockchain + symbol │────>│  Fireblocks API      │          │
│  │  TRON + TRX          │     │  find by currency    │          │
│  └──────────────────────┘     └──────────────────────┘          │
│                                        │                         │
│                                        ▼                         │
│                               ┌──────────────────────┐          │
│                               │  TRX / TRX_TEST      │          │
│                               └──────────────────────┘          │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Data Models

### AssetModel (Canonical)

```python
AssetModel:
  - id: UUID
  - symbol: str          # USDT, ETH, TRX
  - display_name: str    # Tether USD, Ethereum
  - blockchain: str      # ETHEREUM, TRON, BITCOIN
  - network: str         # ERC20, TRC20, NATIVE
  - contract_address: str | None  # 0x... or NULL for native
  - testnet: str | None  # SEPOLIA, SHASTA or NULL for mainnet
  - decimals: int
  - is_native: bool
  - is_active: bool
  - parent_id: UUID | None  # Reference to native coin for tokens
```

### VaultModel

```python
VaultModel:
  - id: UUID
  - provider_vault_id: str  # ID in Fireblocks
  - name: str
  - vault_type: str  # hot, warm, cold, regular
  - is_primary: bool
  - is_active: bool
```

### WalletModel

```python
WalletModel:
  - id: UUID
  - vault_id: UUID
  - asset_id: UUID
  - address: str
  - balance: Decimal
  - pending_amount: Decimal
```

## API Endpoints

### Admin API `/v1/assets/`

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v1/assets/` | Create an asset |
| GET | `/v1/assets/` | List assets |
| GET | `/v1/assets/{id}` | Get an asset |
| PATCH | `/v1/assets/{id}` | Update an asset |
| GET | `/v1/assets/lookup/by-contract/{address}` | Look up by contract |
| GET | `/v1/assets/lookup/native/{blockchain}` | Look up native asset |
| POST | `/v1/assets/resolve/fireblocks` | **Resolve Fireblocks ID** |

### Example: Creating an Asset

```bash
# Native coin
curl -X POST http://localhost:8004/v1/assets/ \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "TRX",
    "display_name": "Tron",
    "blockchain": "TRON",
    "network": "NATIVE",
    "decimals": 6,
    "testnet": "SHASTA",
    "is_native": true
  }'

# Token
curl -X POST http://localhost:8004/v1/assets/ \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "USDT",
    "display_name": "Tether USD",
    "blockchain": "ETHEREUM",
    "network": "ERC20",
    "contract_address": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
    "decimals": 6,
    "is_native": false
  }'
```

### Example: Resolving a Fireblocks ID

```bash
# By blockchain (native)
curl -X POST http://localhost:8004/v1/assets/resolve/fireblocks \
  -H "Content-Type: application/json" \
  -d '{"blockchain": "TRON", "testnet": "SHASTA"}'

# Response: {"fireblocks_asset_id": "TRX_TEST"}

# By contract_address (token)
curl -X POST http://localhost:8004/v1/assets/resolve/fireblocks \
  -H "Content-Type: application/json" \
  -d '{"blockchain": "ETHEREUM", "contract_address": "0xdAC17F958D2ee523a2206206994597C13D831ec7"}'

# Response: {"fireblocks_asset_id": "USDT_ETH"}
```

## Project Structure

```
custody/
├── app/
│   ├── api/
│   │   ├── v1/              # Admin API
│   │   │   ├── assets.py    # Asset CRUD + resolve
│   │   │   └── schemas.py   # Pydantic schemas
│   │   ├── transfer.py      # Transfers
│   │   ├── vault.py         # Vault management
│   │   ├── wallet.py        # Wallets
│   │   └── webhook.py       # Fireblocks webhooks
│   ├── models/
│   │   ├── asset.py         # AssetModel (canonical)
│   │   ├── vault.py         # VaultModel
│   │   ├── wallet.py        # WalletModel
│   │   ├── transaction.py   # TransactionModel
│   │   └── transfer.py      # TransferModel
│   ├── services/
│   │   ├── custody/
│   │   │   ├── fireblocks/
│   │   │   │   ├── service.py    # Fireblocks API client
│   │   │   │   ├── resolver.py   # Asset ID resolver
│   │   │   │   ├── sync.py       # Sync from backend
│   │   │   │   └── utils.py      # Fireblocks ID parsing
│   │   │   └── providers/
│   │   │       ├── base.py       # Base provider interface
│   │   │       └── fireblocks_provider.py
│   │   ├── balance_sync.py  # Balance synchronization
│   │   └── asset_sync.py    # Asset cache refresh
│   └── dao/
│       ├── asset.py         # Asset DAO with resolution
│       └── webhook/         # Webhook processing
├── alembic/                 # Migrations
└── secrets/                 # local Fireblocks key — gitignored, provide your own
```

## Local Setup

### 1. Dependencies (Docker)

```bash
docker compose up -d pg_custody rabbitmq redis
```

### 2. Migrations

```bash
cd custody

# Create a migration
CUSTODY_DB_HOST=localhost CUSTODY_DB_PORT=10432 \
CUSTODY_DB_USER=postgres CUSTODY_DB_PASSWORD=postgres \
CUSTODY_DB_NAME=pg_custody \
uv run alembic revision --autogenerate -m "description"

# Apply migrations
CUSTODY_DB_HOST=localhost CUSTODY_DB_PORT=10432 \
CUSTODY_DB_USER=postgres CUSTODY_DB_PASSWORD=postgres \
CUSTODY_DB_NAME=pg_custody \
uv run alembic upgrade head
```

### 3. Start the Service

```bash
cd custody

CUSTODY_DB_HOST=localhost \
CUSTODY_DB_PORT=10432 \
CUSTODY_DB_USER=postgres \
CUSTODY_DB_PASSWORD=postgres \
CUSTODY_DB_NAME=pg_custody \
API_KEY=your-fireblocks-api-key \
PRIVATE_KEY_FILE=secrets/fireblocks.key \
FIREBLOCKS_SANDBOX=true \
RABBIT_HOST=localhost \
RABBIT_PORT=5672 \
REDIS_HOST=localhost \
REDIS_PORT=6379 \
uv run uvicorn app.main:app --host 0.0.0.0 --port 8004 --reload
```

### 4. Verify

```bash
# Health check
curl http://localhost:8004/health

# List assets
curl http://localhost:8004/v1/assets/
```

## Flow: Wallet Creation

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Backend   │────>│   Custody   │────>│   Resolver  │────>│  Fireblocks │
│             │     │             │     │             │     │             │
│ contract_   │     │ Find Asset  │     │ Resolve FB  │     │ activate_   │
│ address     │     │ by contract │     │ asset ID    │     │ asset()     │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
                           │                   │                   │
                           ▼                   ▼                   ▼
                    ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
                    │ AssetModel  │     │ USDT_ETH    │     │ address:    │
                    │ id: uuid    │     │             │     │ 0x123...    │
                    └─────────────┘     └─────────────┘     └─────────────┘
                                                                   │
                           ┌───────────────────────────────────────┘
                           ▼
                    ┌─────────────┐
                    │ WalletModel │
                    │ asset_id    │
                    │ address     │
                    └─────────────┘
```

## Flow: Webhook Processing

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Fireblocks │────>│   Webhook   │────>│   Reverse   │
│   Webhook   │     │   Handler   │     │   Resolver  │
│             │     │             │     │             │
│ assetId:    │     │ Find asset  │     │ FB ID ──>   │
│ USDT_ETH    │     │ by FB ID    │     │ AssetModel  │
└─────────────┘     └─────────────┘     └─────────────┘
                           │
                           ▼
                    ┌─────────────┐
                    │ Update      │
                    │ Transaction │
                    │ Balance     │
                    └─────────────┘
```

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `CUSTODY_DB_HOST` | PostgreSQL host | localhost |
| `CUSTODY_DB_PORT` | PostgreSQL port | 5432 |
| `CUSTODY_DB_NAME` | Database name | pg_custody |
| `API_KEY` | Fireblocks API Key | - |
| `PRIVATE_KEY_FILE` | Path to Fireblocks private key | - |
| `FIREBLOCKS_SANDBOX` | Use sandbox API | true |
| `RABBIT_HOST` | RabbitMQ host | localhost |
| `REDIS_HOST` | Redis host | localhost |
| `BACKEND_URL` | Backend service URL | http://localhost:8000 |

## Tests

```bash
cd custody
uv run pytest
```
