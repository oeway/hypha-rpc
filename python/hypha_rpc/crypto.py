"""Cryptographic utilities for hypha-rpc signed and encrypted service calls.

This module provides Ed25519 signing/verification and (future) X25519 + AES-256-GCM
encryption, using the `cryptography` package. All functions are designed to produce
output that is interoperable with the Web Crypto API in JavaScript.

The `cryptography` package is an optional dependency. If it is not installed,
importing this module will raise ImportError at call time, not import time,
so that the rest of hypha-rpc continues to work without it.
"""

import logging
import time

logger = logging.getLogger("hypha-rpc")

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PublicFormat,
        PrivateFormat,
        NoEncryption,
    )

    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False


def _require_crypto():
    if not HAS_CRYPTO:
        raise ImportError(
            "The 'cryptography' package is required for signing/encryption. "
            "Install it with: pip install cryptography"
        )


def generate_signing_keypair():
    """Generate an Ed25519 signing keypair.

    Returns:
        tuple: (private_key_bytes, public_key_bytes) as raw 32-byte values.
    """
    _require_crypto()
    private_key = Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(
        Encoding.Raw, PrivateFormat.Raw, NoEncryption()
    )
    public_bytes = private_key.public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw
    )
    return private_bytes, public_bytes


def sign_message(private_key_bytes, data):
    """Sign data with an Ed25519 private key.

    Args:
        private_key_bytes: 32-byte raw Ed25519 private key.
        data: bytes to sign.

    Returns:
        bytes: 64-byte Ed25519 signature.
    """
    _require_crypto()
    private_key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
    return private_key.sign(data)


def verify_signature(public_key_bytes, signature, data):
    """Verify an Ed25519 signature.

    Args:
        public_key_bytes: 32-byte raw Ed25519 public key.
        signature: 64-byte Ed25519 signature.
        data: bytes that were signed.

    Returns:
        bool: True if signature is valid, False otherwise.
    """
    _require_crypto()
    try:
        public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        public_key.verify(signature, data)
        return True
    except Exception:
        return False


def create_signable_data(main_message):
    """Create the canonical byte representation of a message for signing.

    Extracts the signing-relevant fields from segment 1 and serializes them
    with msgpack for deterministic byte output.

    Args:
        main_message: dict containing the RPC message metadata.

    Returns:
        bytes: msgpack-encoded signable fields.
    """
    import msgpack

    # Use sorted keys for deterministic output across Python and JS
    signable = {
        "_ts": main_message.get("_ts"),
        "from": main_message.get("from"),
        "method": main_message.get("method"),
        "to": main_message.get("to"),
        "type": main_message.get("type"),
    }
    return msgpack.packb(signable, use_bin_type=True)


def is_timestamp_valid(timestamp_ms, max_age_seconds=None):
    """Check if a timestamp is within acceptable bounds.

    Args:
        timestamp_ms: Timestamp in milliseconds.
        max_age_seconds: Maximum age in seconds. Defaults to 300 (5 minutes).

    Returns:
        bool: True if the timestamp is within bounds.
    """
    if max_age_seconds is None:
        max_age_seconds = 300
    now_ms = int(time.time() * 1000)
    age_ms = now_ms - timestamp_ms
    # Allow 5 seconds of clock skew into the future
    return -5000 <= age_ms <= max_age_seconds * 1000
