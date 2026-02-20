/**
 * Cryptographic utilities for hypha-rpc signed and encrypted service calls.
 *
 * Uses the Web Crypto API (native in browser and Node.js 20+) for Ed25519
 * signing/verification. All output is interoperable with the Python
 * `cryptography` package.
 *
 * @module crypto
 */

import { encode as msgpack_packb } from "@msgpack/msgpack";

/**
 * Get the SubtleCrypto instance, works in both browser and Node.js.
 * @returns {SubtleCrypto}
 */
function getSubtle() {
  if (typeof globalThis !== "undefined" && globalThis.crypto && globalThis.crypto.subtle) {
    return globalThis.crypto.subtle;
  }
  throw new Error(
    "Web Crypto API (crypto.subtle) is not available in this environment. " +
    "Ed25519 signing requires a modern browser or Node.js 20+."
  );
}

/**
 * Generate an Ed25519 signing keypair.
 * @returns {Promise<{privateKey: Uint8Array, publicKey: Uint8Array}>}
 *   Raw 32-byte private key and 32-byte public key.
 */
export async function generateSigningKeypair() {
  const subtle = getSubtle();
  const keyPair = await subtle.generateKey("Ed25519", true, ["sign", "verify"]);

  // Export raw keys
  const publicRaw = new Uint8Array(
    await subtle.exportKey("raw", keyPair.publicKey)
  );

  // Ed25519 private keys must be exported as PKCS8, then we extract the raw 32 bytes.
  // PKCS8 for Ed25519 has a fixed 16-byte prefix, with the raw key at bytes 16-48.
  const pkcs8 = new Uint8Array(
    await subtle.exportKey("pkcs8", keyPair.privateKey)
  );
  const privateRaw = pkcs8.slice(16, 48);

  return { privateKey: privateRaw, publicKey: publicRaw };
}

/**
 * Import a raw 32-byte Ed25519 private key for signing.
 * @param {Uint8Array} privateKeyBytes - 32-byte raw private key.
 * @returns {Promise<CryptoKey>}
 */
async function importPrivateKey(privateKeyBytes) {
  const subtle = getSubtle();
  // Build PKCS8 wrapper: fixed 16-byte prefix for Ed25519 + 32-byte raw key
  const pkcs8Prefix = new Uint8Array([
    0x30, 0x2e, 0x02, 0x01, 0x00, 0x30, 0x05, 0x06,
    0x03, 0x2b, 0x65, 0x70, 0x04, 0x22, 0x04, 0x20,
  ]);
  const pkcs8 = new Uint8Array(48);
  pkcs8.set(pkcs8Prefix);
  pkcs8.set(privateKeyBytes, 16);
  return subtle.importKey("pkcs8", pkcs8, "Ed25519", false, ["sign"]);
}

/**
 * Import a raw 32-byte Ed25519 public key for verification.
 * @param {Uint8Array} publicKeyBytes - 32-byte raw public key.
 * @returns {Promise<CryptoKey>}
 */
async function importPublicKey(publicKeyBytes) {
  const subtle = getSubtle();
  return subtle.importKey("raw", publicKeyBytes, "Ed25519", false, ["verify"]);
}

/**
 * Sign data with an Ed25519 private key.
 * @param {Uint8Array} privateKeyBytes - 32-byte raw Ed25519 private key.
 * @param {Uint8Array} data - Data to sign.
 * @returns {Promise<Uint8Array>} 64-byte Ed25519 signature.
 */
export async function signMessage(privateKeyBytes, data) {
  const subtle = getSubtle();
  const key = await importPrivateKey(privateKeyBytes);
  const signature = await subtle.sign("Ed25519", key, data);
  return new Uint8Array(signature);
}

/**
 * Verify an Ed25519 signature.
 * @param {Uint8Array} publicKeyBytes - 32-byte raw Ed25519 public key.
 * @param {Uint8Array} signature - 64-byte Ed25519 signature.
 * @param {Uint8Array} data - Data that was signed.
 * @returns {Promise<boolean>} True if valid.
 */
export async function verifySignature(publicKeyBytes, signature, data) {
  const subtle = getSubtle();
  try {
    const key = await importPublicKey(publicKeyBytes);
    return await subtle.verify("Ed25519", key, signature, data);
  } catch {
    return false;
  }
}

/**
 * Create the canonical byte representation of a message for signing.
 *
 * Extracts signing-relevant fields from segment 1 and serializes them
 * with msgpack for deterministic byte output (matching Python's implementation).
 *
 * @param {Object} mainMessage - The RPC message metadata.
 * @returns {Uint8Array} Msgpack-encoded signable fields.
 */
export function createSignableData(mainMessage) {
  // Use sorted keys for deterministic output matching Python's msgpack
  const signable = {
    _ts: mainMessage._ts,
    from: mainMessage.from,
    method: mainMessage.method,
    to: mainMessage.to,
    type: mainMessage.type,
  };
  return msgpack_packb(signable, { sortKeys: true });
}

/**
 * Check if a timestamp is within acceptable bounds.
 * @param {number} timestampMs - Timestamp in milliseconds.
 * @param {number} [maxAgeSeconds=300] - Maximum age in seconds.
 * @returns {boolean} True if the timestamp is within bounds.
 */
export function isTimestampValid(timestampMs, maxAgeSeconds = 300) {
  const nowMs = Date.now();
  const ageMs = nowMs - timestampMs;
  // Allow 5 seconds of clock skew into the future
  return -5000 <= ageMs && ageMs <= maxAgeSeconds * 1000;
}
