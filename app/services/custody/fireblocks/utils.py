def parse_fireblocks_asset(asset_id: str, fb_asset: dict) -> dict | None:
    """
    Автоматически парсит Fireblocks asset ID в наш формат.

    Примеры:
    - ETH -> ETHEREUM/ETH/NATIVE
    - ETH_TEST6 -> ETHEREUM/ETH/SEPOLIA
    - USDT_ERC20 -> ETHEREUM/USDT/ERC20
    - TRX -> TRON/TRX/NATIVE
    - USDT_TRX_TEST4 -> TRON/USDT/SHASTA
    - BNB_BSC -> BSC/BNB/NATIVE
    - USDT_BSC -> BSC/USDT/BEP20
    """
    asset_id_upper = asset_id.upper()
    decimals = fb_asset.get("decimals", 18)

    blockchain = None
    currency = None
    network = "NATIVE"
    is_testnet = False

    testnet_patterns = {
        "TEST6": "HOLESKY",
        "TEST5": "SEPOLIA",
        "TEST4": "SHASTA",
        "TEST": "TESTNET",
        "AMOY": "AMOY",
        "MUMBAI": "MUMBAI",
        "DEVNET": "DEVNET",
    }

    for pattern, net in testnet_patterns.items():
        if pattern in asset_id_upper:
            network = net
            is_testnet = True
            break

    if asset_id_upper.startswith("ETH"):
        blockchain = "ETHEREUM"
        currency = "ETH"
    elif asset_id_upper.startswith("BTC"):
        blockchain = "BITCOIN"
        currency = "BTC"
    elif asset_id_upper.startswith("TRX") or "_TRX" in asset_id_upper:
        blockchain = "TRON"
        if asset_id_upper.startswith("TRX"):
            currency = "TRX"
    elif asset_id_upper.startswith("BNB") or "_BSC" in asset_id_upper:
        blockchain = "BSC"
        if asset_id_upper.startswith("BNB"):
            currency = "BNB"
    elif "POLYGON" in asset_id_upper or asset_id_upper.startswith("MATIC"):
        blockchain = "POLYGON"
        if asset_id_upper.startswith("MATIC") or asset_id_upper.startswith("AMOY"):
            currency = "MATIC"
    elif asset_id_upper.startswith("SOL"):
        blockchain = "SOLANA"
        currency = "SOL"
    elif asset_id_upper.startswith("TON"):
        blockchain = "TON"
        if "_USDT" in asset_id_upper:
            currency = "USDT"
            network = "JETTON"
        else:
            currency = "TON"
    elif asset_id_upper.startswith("XRP"):
        blockchain = "XRP"
        currency = "XRP"
    elif asset_id_upper.startswith("DOGE"):
        blockchain = "DOGECOIN"
        currency = "DOGE"
    elif asset_id_upper.startswith("LTC"):
        blockchain = "LITECOIN"
        currency = "LTC"

    if asset_id_upper.startswith("USDT"):
        currency = "USDT"
        if (
            "_ETH" in asset_id_upper
            or "_ERC20" in asset_id_upper
            or blockchain == "ETHEREUM"
        ):
            blockchain = "ETHEREUM"
            network = network if is_testnet else "ERC20"
        elif "_TRX" in asset_id_upper or blockchain == "TRON":
            blockchain = "TRON"
            network = "SHASTA" if is_testnet else "TRC20"
        elif "_BSC" in asset_id_upper or blockchain == "BSC":
            blockchain = "BSC"
            network = "BEP20"
        elif "_POLYGON" in asset_id_upper or blockchain == "POLYGON":
            blockchain = "POLYGON"
            network = network if is_testnet else "POLYGON"
    elif asset_id_upper.startswith("USDC"):
        currency = "USDC"
        if "_POLYGON" in asset_id_upper:
            blockchain = "POLYGON"
            network = "POLYGON"
        elif (
            "_ETH" in asset_id_upper
            or "_ERC20" in asset_id_upper
            or asset_id_upper == "USDC"
            or blockchain == "ETHEREUM"
        ):
            blockchain = "ETHEREUM"
            network = network if is_testnet else "ERC20"
        elif "_TRX" in asset_id_upper:
            blockchain = "TRON"
            network = "TRC20"
    elif asset_id_upper.startswith("DAI"):
        currency = "DAI"
        if (
            "_ETH" in asset_id_upper
            or asset_id_upper == "DAI"
            or blockchain == "ETHEREUM"
        ):
            blockchain = "ETHEREUM"
            network = network if is_testnet else "ERC20"

    if not blockchain or not currency:
        return None

    # Fireblocks всегда присылает type (например, BEP20, TRON_TRC20) — используем его как сеть,
    # а ранее вычисленное значение оставляем как фолбэк.
    fb_type = fb_asset.get("type")
    return {
        "blockchain": blockchain,
        "currency": currency,
        "network": fb_type or network,
        "decimals": decimals,
        "is_testnet": is_testnet,
    }


def mapping_native_tokens():
    """
    Mapping of Fireblocks native symbols to Fireblocks asset IDs and metadata.
    """
    return {
        "prod": {
            "ETH": {
                "asset_id": "ETH",
                "blockchain": "ETHEREUM",
                "currency": "ETH",
                "network": "NATIVE",
                "is_testnet": False,
            },
            "TRX": {
                "asset_id": "TRX",
                "blockchain": "TRON",
                "currency": "TRX",
                "network": "NATIVE",
                "is_testnet": False,
            },
            "TON": {
                "asset_id": "TON",
                "blockchain": "TON",
                "currency": "TON",
                "network": "NATIVE",
                "is_testnet": False,
            },
            "BNB": {
                "asset_id": "BNB_BSC",
                "blockchain": "BSC",
                "currency": "BNB",
                "network": "BSC",
                "is_testnet": False,
            },
            "MATIC": {
                "asset_id": "MATIC_POLYGON",
                "blockchain": "POLYGON",
                "currency": "MATIC",
                "network": "POLYGON",
                "is_testnet": False,
            },
            "BTC": {
                "asset_id": "BTC",
                "blockchain": "BITCOIN",
                "currency": "BTC",
                "network": "NATIVE",
                "is_testnet": False,
            },
            "SOL": {
                "asset_id": "SOL",
                "blockchain": "SOLANA",
                "currency": "SOL",
                "network": "NATIVE",
                "is_testnet": False,
            },
        },
        "dev": {
            "ETH": {
                "asset_id": "ETH_TEST5",
                "blockchain": "ETHEREUM",
                "currency": "ETH",
                "network": "SEPOLIA",
                "is_testnet": True,
            },
            "TRX": {
                "asset_id": "TRX_TEST",
                "blockchain": "TRON",
                "currency": "TRX",
                "network": "SHASTA",
                "is_testnet": True,
            },
            "TON": {
                "asset_id": "TON_TEST",
                "blockchain": "TON",
                "currency": "TON",
                "network": "TESTNET",
                "is_testnet": True,
            },
            "BNB": {
                "asset_id": "BNB_TEST",
                "blockchain": "BSC",
                "currency": "BNB",
                "network": "TESTNET",
                "is_testnet": True,
            },
            "MATIC": {
                "asset_id": "AMOY_POLYGON_TEST",
                "blockchain": "POLYGON",
                "currency": "MATIC",
                "network": "AMOY",
                "is_testnet": True,
            },
            "BTC": {
                "asset_id": "BTC_TEST",
                "blockchain": "BITCOIN",
                "currency": "BTC",
                "network": "TESTNET",
                "is_testnet": True,
            },
            "SOL": {
                "asset_id": "SOL_TEST",
                "blockchain": "SOLANA",
                "currency": "SOL",
                "network": "DEVNET",
                "is_testnet": True,
            },
        },
    }
