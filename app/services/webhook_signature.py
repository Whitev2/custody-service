"""
Service for validating Fireblocks webhook signature.

Fireblocks signs all webhook events using RSA-SHA512.
Signature is passed in Fireblocks-Signature header.

Documentation:
https://developers.fireblocks.com/reference/webhooks-gettingstarted-validatingevents
"""

import base64
from os import getenv

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend
from cryptography.exceptions import InvalidSignature

from app.config import log


# Fireblocks public keys for different environments
# US Mainnet & Testnet
FIREBLOCKS_PUBLIC_KEY_US = """-----BEGIN PUBLIC KEY-----
MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEA0+6wd9OJQpK60ZI7qnZG
jjQ0wNFUHfRv85Tdyek8+ahlg1Ph8uhwl4N6DZw5LwLXhNjzAbQ8LGPxt36RUZl5
YlxTru0jZNKx5lslR+H4i936A4pKBjgiMmSkVwXD9HcfKHTp70GQ812+J0Fvti/v
4nrrUpc011Wo4F6omt1QcYsi4GTI5OsEbeKQ24BtUd6Z1Nm/EP7PfPxeb4CP8KOH
clM8K7OwBUfWrip8Ptljjz9BNOZUF94iyjJ/BIzGJjyCntho64ehpUYP8UJykLVd
CGcu7sVYWnknf1ZGLuqqZQt4qt7cUUhFGielssZP9N9x7wzaAIFcT3yQ+ELDu1SZ
dE4lZsf2uMyfj58V8GDOLLE233+LRsRbJ083x+e2mW5BdAGtGgQBusFfnmv5Bxqd
HgS55hsna5725/44tvxll261TgQvjGrTxwe7e5Ia3d2Syc+e89mXQaI/+cZnylNP
SwCCvx8mOM847T0XkVRX3ZrwXtHIA25uKsPJzUtksDnAowB91j7RJkjXxJcz3Vh1
4k182UFOTPRW9jzdWNSyWQGl/vpe9oQ4c2Ly15+/toBo4YXJeDdDnZ5c/O+KKadc
IMPBpnPrH/0O97uMPuED+nI6ISGOTMLZo35xJ96gPBwyG5s2QxIkKPXIrhgcgUnk
tSM7QYNhlftT4/yVvYnk0YcCAwEAAQ==
-----END PUBLIC KEY-----"""

# EU & EU2 Mainnet & Testnet
FIREBLOCKS_PUBLIC_KEY_EU = """-----BEGIN PUBLIC KEY-----
MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEA6hLRQL0jPf5OEuaDYGjO
xSyaYIlv08S0+4giiwgKSfV3Onc5hn03mvE0znzaUq2ReSxi9KYDdMYFfzf1uwF7
7kYy2MY0oTYGdQb+PS4Ym4R4tgZ2otuoAXt8YRKq2maWyguFiaowMcYwwAVQv8JB
afIm6Jq1nI6v1mEDVX065ePlBlAt+BGAqr6ahPxnaIz3L4eztpuNrt5nTbSxs7eF
aqQx1p56W1nl3Hl0V3tLkaXbuVtbFNR/mGMInrkPnpsG+mt35b9vmqAOvLPI0Cx1
59uVeEs62Hj1AOCRyT6SuwIaFynRj2KnD42ioQtkodHQ0xDtgdiYGsxuwQ9vTIe7
5oLsL8gBDeX5gdcTfSZhfGjZ7RggLNJ7vCAbYKMuUOdgWVMYnJfrhNLCq3zDSZPO
+H0x5m/Yeq/Hn5o7xCmLNT3qARfwDd5IHfQyXqVYB6TMU75xqH5fdSRw0iMdoPyL
ALnr9/JT0av3qssNMRdWCXr+j9Ys3NkfcbU/a49657mg8e2QGSkl9w39csEKojnr
omUz25szIL8CcXLmc5cAmnimFCe4L7UT4mvVP3+fOo+cbc/82zqA8tsSwd2Y93/6
ueGnNZD9V5rewrKjmdPfrwoI2gntzc8QJUu+nxAWhoqHV91AQeglu6WIF/DiEJC5
WPoNk2SdlAuA6RYmgB2YyikCAwEAAQ==
-----END PUBLIC KEY-----"""

# Developer Sandbox
FIREBLOCKS_PUBLIC_KEY_SANDBOX = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAw+fZuC+0vDYTf8fYnCN6
71iHg98lPHBmafmqZqb+TUexn9sH6qNIBZ5SgYFxFK6dYXIuJ5uoORzihREvZVZP
8DphdeKOMUrMr6b+Cchb2qS8qz8WS7xtyLU9GnBn6M5mWfjkjQr1jbilH15Zvcpz
ECC8aPUAy2EbHpnr10if2IHkIAWLYD+0khpCjpWtsfuX+LxqzlqQVW9xc6z7tshK
eCSEa6Oh8+ia7Zlu0b+2xmy2Arb6xGl+s+Rnof4lsq9tZS6f03huc+XVTmd6H2We
WxFMfGyDCX2akEg2aAvx7231/6S0vBFGiX0C+3GbXlieHDplLGoODHUt5hxbPJnK
IwIDAQAB
-----END PUBLIC KEY-----"""


class WebhookSignatureValidator:
    """Fireblocks webhook signature validator."""

    def __init__(self, environment: str = "sandbox"):
        """
        Initialize validator.

        Args:
            environment: Fireblocks environment ('sandbox', 'us', 'eu')
        """
        self.environment = environment
        self._public_key = self._load_public_key(environment)

    @staticmethod
    def _load_public_key(environment: str):
        """Load public key for specified environment."""
        key_pem = {
            "sandbox": FIREBLOCKS_PUBLIC_KEY_SANDBOX,
            "us": FIREBLOCKS_PUBLIC_KEY_US,
            "eu": FIREBLOCKS_PUBLIC_KEY_EU,
        }.get(environment, FIREBLOCKS_PUBLIC_KEY_SANDBOX)

        return serialization.load_pem_public_key(
            key_pem.encode(), backend=default_backend()
        )

    def verify_signature(self, body: bytes, signature: str) -> bool:
        """
        Verify webhook signature.

        Fireblocks uses:
        Fireblocks-Signature: Base64(RSA512(_WEBHOOK_PRIVATE_KEY_, SHA512(eventBody)))

        Args:
            body: Request body in bytes
            signature: Signature from Fireblocks-Signature header

        Returns:
            True if signature is valid, False otherwise
        """
        try:
            # Decode signature from Base64
            signature_bytes = base64.b64decode(signature)

            # Verify signature
            self._public_key.verify(
                signature_bytes,
                body,
                padding.PKCS1v15(),
                hashes.SHA512(),
            )

            log.debug("✅ Webhook signature is valid")
            return True

        except InvalidSignature:
            log.warning("❌ Invalid webhook signature")
            return False
        except Exception as e:
            log.error(f"❌ Error verifying signature: {e}")
            return False


def get_webhook_validator() -> WebhookSignatureValidator:
    """
    Get webhook signature validator.

    Environment is determined from FIREBLOCKS_ENVIRONMENT variable.
    Defaults to sandbox.
    """
    environment = getenv("FIREBLOCKS_ENVIRONMENT", "sandbox").lower()
    return WebhookSignatureValidator(environment)
