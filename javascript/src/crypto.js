/**
 * Cryptographic utilities for hypha-rpc end-to-end encrypted service calls.
 *
 * Uses tweetnacl (libsodium-compatible) for:
 * - Curve25519 ECDH + XSalsa20-Poly1305 authenticated encryption (crypto_box)
 *
 * All output is interoperable with the Python PyNaCl package.
 *
 * tweetnacl is an optional dependency. If it is not installed, the hex-conversion
 * helpers still work, but encryption/decryption functions will throw a clear error.
 *
 * @module crypto
 */

let _nacl = null;

function _requireCrypto() {
  if (_nacl) return _nacl;
  try {
    // Use require() for synchronous loading; works in both bundled and Node.js contexts
    _nacl = require("tweetnacl");
    // Handle ES module default export
    if (_nacl.default) _nacl = _nacl.default;
    return _nacl;
  } catch (e) {
    throw new Error(
      "The 'tweetnacl' package is required for encryption. " +
        "Install it with: npm install tweetnacl",
    );
  }
}

/**
 * Convert a 32-byte public key to a 64-character hex string.
 * @param {Uint8Array} publicKeyBytes - 32-byte raw public key.
 * @returns {string} 64-character hex string.
 */
export function publicKeyToHex(publicKeyBytes) {
  return Array.from(publicKeyBytes, (b) => b.toString(16).padStart(2, "0")).join(
    "",
  );
}

/**
 * Convert a 64-character hex string to a 32-byte public key.
 * @param {string} hexString - 64-character hex string.
 * @returns {Uint8Array} 32-byte raw public key.
 */
export function publicKeyFromHex(hexString) {
  const bytes = new Uint8Array(hexString.length / 2);
  for (let i = 0; i < bytes.length; i++) {
    bytes[i] = parseInt(hexString.substr(i * 2, 2), 16);
  }
  return bytes;
}

// --- Curve25519 + XSalsa20-Poly1305 Encryption (crypto_box) ---

/**
 * Generate a Curve25519 keypair for authenticated encryption.
 * @returns {Promise<{privateKey: Uint8Array, publicKey: Uint8Array}>}
 *   Raw 32-byte private key and 32-byte public key.
 */
export async function generateEncryptionKeypair() {
  const nacl = _requireCrypto();
  const kp = nacl.box.keyPair();
  return { privateKey: kp.secretKey, publicKey: kp.publicKey };
}

/**
 * Encrypt plaintext using crypto_box (Curve25519 + XSalsa20-Poly1305).
 * @param {Uint8Array} myPrivateBytes - 32-byte raw Curve25519 private key.
 * @param {Uint8Array} theirPublicBytes - 32-byte raw Curve25519 public key.
 * @param {Uint8Array} plaintext - Data to encrypt.
 * @returns {Promise<{nonce: Uint8Array, ciphertext: Uint8Array}>}
 *   nonce is 24 bytes; ciphertext includes 16-byte Poly1305 auth tag.
 */
export async function encryptPayload(myPrivateBytes, theirPublicBytes, plaintext) {
  const nacl = _requireCrypto();
  const nonce = nacl.randomBytes(nacl.box.nonceLength); // 24 bytes
  const ciphertext = nacl.box(plaintext, nonce, theirPublicBytes, myPrivateBytes);
  return { nonce, ciphertext };
}

/**
 * Decrypt ciphertext using crypto_box (Curve25519 + XSalsa20-Poly1305).
 * @param {Uint8Array} myPrivateBytes - 32-byte raw Curve25519 private key.
 * @param {Uint8Array} theirPublicBytes - 32-byte raw Curve25519 public key.
 * @param {Uint8Array} nonce - 24-byte nonce used during encryption.
 * @param {Uint8Array} ciphertext - Data to decrypt (includes 16-byte Poly1305 auth tag).
 * @returns {Promise<Uint8Array>} Decrypted plaintext.
 * @throws {Error} If decryption/authentication fails.
 */
export async function decryptPayload(myPrivateBytes, theirPublicBytes, nonce, ciphertext) {
  const nacl = _requireCrypto();
  const plaintext = nacl.box.open(ciphertext, nonce, theirPublicBytes, myPrivateBytes);
  if (plaintext === null) {
    throw new Error("Decryption failed");
  }
  return plaintext;
}
