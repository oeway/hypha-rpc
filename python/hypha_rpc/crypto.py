"""Cryptographic utilities for hypha-rpc end-to-end encrypted service calls.

This module provides:
- X25519 (Curve25519) ECDH + XSalsa20-Poly1305 authenticated encryption
  via libsodium's crypto_box (PyNaCl)

All functions use PyNaCl and produce output that is interoperable with
tweetnacl in JavaScript.

PyNaCl is an optional dependency. If it is not installed, importing this
module will raise ImportError at call time, not import time, so that the
rest of hypha-rpc continues to work without it.
Install with: pip install hypha-rpc[encryption]
"""

import logging

logger = logging.getLogger("hypha-rpc")

try:
    from nacl.public import PrivateKey, PublicKey, Box
    from nacl.utils import random as nacl_random

    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False


def _require_crypto():
    if not HAS_CRYPTO:
        raise ImportError(
            "The 'PyNaCl' package is required for encryption. "
            "Install it with: pip install hypha-rpc[encryption]"
        )


def public_key_to_hex(public_key_bytes):
    """Convert a 32-byte public key to a 64-character hex string."""
    return public_key_bytes.hex()


def public_key_from_hex(hex_string):
    """Convert a 64-character hex string to a 32-byte public key."""
    return bytes.fromhex(hex_string)


def generate_encryption_keypair():
    """Generate a Curve25519 keypair for authenticated encryption.

    Returns:
        tuple: (private_key_bytes, public_key_bytes) as raw 32-byte values.
    """
    _require_crypto()
    key = PrivateKey.generate()
    return bytes(key), bytes(key.public_key)


def encrypt_payload(my_private_bytes, their_public_bytes, plaintext):
    """Encrypt plaintext using libsodium crypto_box (Curve25519 + XSalsa20-Poly1305).

    Args:
        my_private_bytes: 32-byte raw Curve25519 private key.
        their_public_bytes: 32-byte raw Curve25519 public key.
        plaintext: bytes to encrypt.

    Returns:
        tuple: (nonce, ciphertext) where nonce is 24 bytes and
               ciphertext includes the 16-byte Poly1305 auth tag.
    """
    _require_crypto()
    box = Box(PrivateKey(my_private_bytes), PublicKey(their_public_bytes))
    nonce = nacl_random(Box.NONCE_SIZE)  # 24 bytes
    encrypted = box.encrypt(plaintext, nonce)
    # encrypted.ciphertext is just the ciphertext without the prepended nonce
    return nonce, encrypted.ciphertext


def decrypt_payload(my_private_bytes, their_public_bytes, nonce, ciphertext):
    """Decrypt ciphertext using libsodium crypto_box (Curve25519 + XSalsa20-Poly1305).

    Args:
        my_private_bytes: 32-byte raw Curve25519 private key.
        their_public_bytes: 32-byte raw Curve25519 public key.
        nonce: 24-byte nonce used during encryption.
        ciphertext: bytes to decrypt (includes 16-byte Poly1305 auth tag).

    Returns:
        bytes: decrypted plaintext.

    Raises:
        nacl.exceptions.CryptoError: if decryption/authentication fails.
    """
    _require_crypto()
    box = Box(PrivateKey(my_private_bytes), PublicKey(their_public_bytes))
    return box.decrypt(ciphertext, nonce)
