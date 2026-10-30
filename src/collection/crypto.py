"""Keys derived from the one secret, so that two uses never share a key.

There is a single secret to manage, `ANONYMIZATION_SALT`, and more than one thing that
needs one: the pseudonyms that stand in for a tenant's customers, and the signature on a
chat identity token. Using the secret itself for both would make them the same key, and
the same key in two places means a leak from either is a leak from both. Deriving instead
costs one HMAC and keeps a single thing to rotate.

The purpose is framed with the length of each part rather than joined on a separator:
`("a:b",)` and `("a", "b")` must not derive the same key, or the namespacing a caller
believes it has is only a convention about which characters it avoided.
"""

import hashlib
import hmac

from collection.config import settings


def derive_key(*purpose: str) -> bytes:
    """A key that belongs to `purpose` alone, under the configured salt."""
    framed = b"".join(len(part.encode()).to_bytes(4, "big") + part.encode() for part in purpose)
    return hmac.new(settings.anonymization_salt.encode(), framed, hashlib.sha256).digest()
