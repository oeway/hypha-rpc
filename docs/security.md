# Hypha RPC Security: End-to-End Encryption

## Table of Contents

- [Executive Summary](#executive-summary)
- [Threat Model](#threat-model)
- [Architecture](#architecture)
- [Cryptographic Design](#cryptographic-design)
- [Key Management](#key-management)
- [Wire Format](#wire-format)
- [Access Control: Trusted Keys](#access-control-trusted-keys)
- [Python API Reference](#python-api-reference)
- [JavaScript API Reference](#javascript-api-reference)
- [Usage Examples](#usage-examples)
- [Security Properties](#security-properties)
- [Known Limitations](#known-limitations)
- [Comparison with Alternatives](#comparison-with-alternatives)
- [Deployment Guidance](#deployment-guidance)
- [Future Work](#future-work)

---

## Executive Summary

Hypha RPC provides **opt-in end-to-end encryption** for RPC calls between clients connected to a Hypha server. When enabled, the Hypha server — which acts as a message relay — cannot read, modify, or forge the encrypted payloads exchanged between clients.

The encryption uses **libsodium's `crypto_box`** (Curve25519 ECDH + XSalsa20-Poly1305 authenticated encryption), implemented via:

- **Python**: [PyNaCl](https://pynacl.readthedocs.io/) (libsodium bindings)
- **JavaScript**: [tweetnacl](https://tweetnacl.js.org/) (pure JS, libsodium-compatible)

Key exchange is performed **out-of-band** — public keys are never distributed through the server, preventing man-in-the-middle attacks by a compromised relay.

---

## Threat Model

### What We Protect Against

| Threat | Protection |
|--------|------------|
| **Curious server** reads RPC arguments/return values | Payload encrypted with XSalsa20-Poly1305; server sees only routing metadata |
| **Compromised server** modifies payloads | Poly1305 MAC detects tampering; modified ciphertext fails authentication |
| **Compromised server** forges messages between clients | Server does not possess either client's private key; cannot produce valid ciphertext |
| **Unauthorized caller** invokes a protected service | `trusted_keys` allowlist rejects callers whose public key is not listed |
| **Eavesdropper** on the network reads payloads | WSS (TLS) protects the transport; E2E encryption protects against the relay itself |

### What We Do NOT Protect Against

| Threat | Why |
|--------|-----|
| **Compromised client endpoint** | If an attacker has access to a client's memory, they have the private key |
| **Traffic analysis** | Message sizes, timing, and routing metadata (from, to, method name) are visible to the server |
| **Denial of service** | The server can drop, delay, or misroute messages |
| **Key compromise with recorded traffic** | No forward secrecy — if a private key is later compromised, previously recorded encrypted traffic can be decrypted |
| **Replay attacks** | Random nonces prevent nonce reuse, but there is no sequence numbering; the RPC promise/session system provides implicit replay resistance |

### Trust Assumptions

1. **Clients exchange public keys out-of-band.** The server never distributes keys. Clients must verify key authenticity through a trusted channel (config files, secure messaging, physical exchange, etc.).
2. **The server routes messages correctly.** We trust the server to deliver messages to the intended recipient. It cannot read or modify encrypted content, but it could drop messages.
3. **Private keys are held in memory only.** Keys exist for the lifetime of the connection. They are not persisted to disk by hypha-rpc.

---

## Architecture

Hypha RPC uses a **two-segment msgpack protocol**:

```
[msgpack(segment_1)][msgpack(segment_2)]
   routing info          payload (args/kwargs/promise)
```

- **Segment 1** contains routing fields (`type`, `from`, `to`, `method`, `session`) used by the server to deliver the message
- **Segment 2** contains the payload (`args`, `kwargs`, `promise`) — the actual function arguments and return values

The server is a **pure message broker**: it reads segment 1 for routing and forwards the entire message without inspecting segment 2. This makes E2E encryption of segment 2 architecturally clean.

### Encryption Scope

```
                    Cleartext              Encrypted
                 ┌─────────────┐    ┌───────────────────┐
Message:         │  Segment 1  │    │    Segment 2      │
                 │  type, from │    │  args, kwargs,     │
                 │  to, method │    │  promise data,     │
                 │  session    │    │  return values     │
                 │  _enc info  │    │                    │
                 └─────────────┘    └───────────────────┘
                       │                     │
                 Server can read       Server CANNOT read
                 (needed for routing)  (encrypted payload)
```

When encryption is active, segment 2 is replaced with an encryption envelope:

```python
{
    "_enc": {
        "v": 2,                    # Protocol version
        "pub": sender_public_key,  # 32-byte Curve25519 public key
        "nonce": random_nonce,     # 24-byte random nonce
    },
    "data": ciphertext,            # XSalsa20-Poly1305 encrypted payload
}
```

---

## Cryptographic Design

### Primitives

| Component | Algorithm | Standard |
|-----------|-----------|----------|
| Key agreement | Curve25519 ECDH | DJB, 2006; RFC 7748 |
| Authenticated encryption | XSalsa20-Poly1305 | DJB; NaCl `crypto_box` |
| Nonce | 24-byte random | Per-message, from OS CSPRNG |

### Why `crypto_box`

NaCl's `crypto_box` combines Curve25519 key agreement with XSalsa20-Poly1305 authenticated encryption in a single, misuse-resistant construction designed by Daniel J. Bernstein. It is:

- **Battle-tested**: Used by libsodium, WireGuard, Signal (partially), miniLock, and thousands of applications
- **Audited**: libsodium has undergone multiple independent security audits
- **Authenticated**: The Poly1305 MAC ensures both confidentiality and integrity — any modification to the ciphertext is detected
- **Mutually authenticating**: Both sides prove knowledge of their private key during the ECDH exchange. The server cannot produce valid ciphertext for either party

### Libraries

| Platform | Library | Notes |
|----------|---------|-------|
| Python | [PyNaCl](https://pynacl.readthedocs.io/) >= 1.5.0 | Binds to libsodium; prebuilt wheels for all platforms |
| JavaScript | [tweetnacl](https://tweetnacl.js.org/) ^1.0.3 | Pure JavaScript; libsodium-compatible; works in browser + Node.js |

Both libraries are **optional dependencies** — they are only required when E2E encryption is enabled:

```bash
# Python
pip install hypha-rpc[encryption]

# JavaScript (npm installs it automatically as an optional dependency;
# or install explicitly if needed)
npm install tweetnacl
```

Both libraries produce **bit-identical output** for the same inputs. This is verified by the cross-platform test suite (JavaScript client ↔ Python server).

### Encryption Flow

```
Caller (Client A)                              Service (Client B)
─────────────────                              ──────────────────

1. Has own keypair:                            1. Has own keypair:
   (privA, pubA)                                  (privB, pubB)

2. Knows pubB (out-of-band)                   2. Knows nothing about A yet

3. Serialize payload:
   plaintext = msgpack(args, kwargs)

4. Generate random 24-byte nonce

5. Encrypt:
   ciphertext = crypto_box(
     plaintext, nonce,
     pubB,     ← target's public key
     privA     ← own private key
   )

6. Send message:
   segment_1: { from, to, method,
     _enc: { v:2, pub:pubA, nonce } }
   segment_2: { data: ciphertext }
                                               7. Receive message, read _enc
        ──── via Hypha server ────►
                                               8. Extract pubA from _enc.pub

                                               9. Decrypt:
                                                  plaintext = crypto_box_open(
                                                    ciphertext, nonce,
                                                    pubA,     ← caller's pub
                                                    privB     ← own private key
                                                  )

                                              10. Unpack: args, kwargs = msgpack(plaintext)

                                              11. Check trusted_keys (if configured):
                                                  Is hex(pubA) in the allowlist?

                                              12. Execute service function

                                              13. Encrypt return value using pubA
                                                  (via _expand_promise callback)
```

### Return Value Encryption

When a caller sends an encrypted request, the response is also encrypted. The RPC layer achieves this by attaching the caller's public key (`pubA`) to the promise's resolve/reject callbacks. When the service function returns (or raises an error), the return value is encrypted with the caller's public key before being sent back.

---

## Key Management

### Key Generation

Each client generates a Curve25519 keypair when `encryption: True` is set in the connection config:

```python
# Python — automatic on connect
server = await connect_to_server({
    "server_url": "https://hypha.aicell.io",
    "encryption": True,
})

# Manual generation
from hypha_rpc.crypto import generate_encryption_keypair, public_key_to_hex
private_key, public_key = generate_encryption_keypair()
print(public_key_to_hex(public_key))  # 64-char hex string
```

```javascript
// JavaScript — automatic on connect
const server = await connectToServer({
    server_url: "https://hypha.aicell.io",
    encryption: true,
});

// Manual generation
import { generateEncryptionKeypair, publicKeyToHex } from "hypha-rpc";
const { privateKey, publicKey } = await generateEncryptionKeypair();
console.log(publicKeyToHex(publicKey));  // 64-char hex string
```

### Key Representation

- **Raw**: 32 bytes (Uint8Array in JS, bytes in Python)
- **Hex**: 64-character lowercase hexadecimal string (used in APIs and configuration)

### Out-of-Band Key Exchange

**The server never distributes encryption keys.** This is a deliberate design choice to prevent MITM attacks by a compromised server.

Public keys must be exchanged through a trusted channel:

| Method | Suitable For |
|--------|-------------|
| Configuration file | Static services with known keypairs |
| Environment variable | Container/cloud deployments |
| Secure messaging (Signal, etc.) | Manual key exchange between operators |
| Service discovery with pinning | First-use trust, then pin the key |
| Hardware token / QR code | High-security environments |

### Key Lifecycle

- **Creation**: On `connect_to_server()` with `encryption: True`
- **Duration**: Lifetime of the connection
- **Storage**: In-memory only (not persisted by hypha-rpc)
- **Rotation**: Reconnect to generate a new keypair
- **Revocation**: Remove from `trusted_keys` list; reconnect

---

## Wire Format

### Protocol Version

The current encryption protocol is **version 2** (`_enc.v = 2`), using NaCl `crypto_box`.

### Encrypted Message Structure

```
Standard message (no encryption):
  [msgpack(segment_1)][msgpack(segment_2)]

Encrypted message:
  [msgpack(segment_1)][msgpack(encrypted_envelope)]
```

**Segment 1** (cleartext, used for routing):

```python
{
    "type": "method",         # Message type
    "from": "ws/client-a",    # Sender
    "to": "ws/client-b",      # Recipient
    "method": "svc.func",     # Target method
    "session": "...",         # Call session
    # ... other routing fields
}
```

**Encrypted envelope** (replaces standard segment 2):

```python
{
    "_enc": {
        "v": 2,               # Protocol version (2 = NaCl crypto_box)
        "pub": bytes(32),      # Sender's Curve25519 public key
        "nonce": bytes(24),    # Random nonce (XSalsa20)
    },
    "data": bytes(...),        # Ciphertext (XSalsa20-Poly1305)
                               # = encrypt(msgpack(original_segment_2))
                               # Includes 16-byte Poly1305 auth tag
}
```

### Ciphertext Overhead

| Component | Size |
|-----------|------|
| Nonce | 24 bytes (in `_enc.nonce`) |
| Poly1305 auth tag | 16 bytes (appended to ciphertext) |
| Sender public key | 32 bytes (in `_enc.pub`) |
| **Total overhead per message** | **~72 bytes + msgpack framing** |

---

## Access Control: Trusted Keys

The `trusted_keys` feature provides **caller authentication** based on encryption public keys. When a service is registered with `trusted_keys`, only callers whose Curve25519 public key is in the allowlist can invoke the service.

### How It Works

1. Service registers with `trusted_keys: ["aabb...", "ccdd..."]` (list of hex public keys)
2. Caller sends an encrypted request (which includes their public key in `_enc.pub`)
3. Service decrypts the request, then checks: is `hex(caller_pub)` in `trusted_keys`?
4. If not in the list → `PermissionError` is raised
5. If no encryption → `PermissionError` ("Encryption required")

### Context Injection

When `require_context: True` is set alongside encryption, the decrypted context includes:

```python
context = {
    "from": "workspace/client-id",
    "to": "workspace/service-id",
    "ws": "workspace-name",
    "user": { ... },
    # Added by E2E encryption:
    "encryption": True,
    "caller_public_key": "aabb...",  # Hex-encoded Curve25519 public key
}
```

This allows service functions to implement fine-grained authorization:

```python
async def transfer(amount, context=None):
    if context.get("caller_public_key") == admin_key:
        return do_admin_transfer(amount)
    else:
        return do_user_transfer(amount)
```

---

## Python API Reference

### Connection Config

```python
server = await connect_to_server({
    "server_url": "https://hypha.aicell.io",
    "encryption": True,         # Enable E2E encryption
})
```

### Service Registration

```python
await server.register_service({
    "id": "my-service",
    "config": {
        "trusted_keys": [        # Optional: restrict to these callers
            "aabbccdd...",       # 64-char hex public keys
        ],
    },
    "my_method": my_function,
})
```

### Service Discovery with Encryption

```python
svc = await server.get_service("my-service",
    encryption_public_key="aabbccdd..."  # Target's public key (hex)
)
result = await svc.my_method(arg1, arg2)  # Encrypted transparently
```

### Key Utilities

```python
from hypha_rpc.crypto import (
    generate_encryption_keypair,  # () -> (private_bytes, public_bytes)
    encrypt_payload,              # (my_priv, their_pub, plaintext) -> (nonce, ciphertext)
    decrypt_payload,              # (my_priv, their_pub, nonce, ciphertext) -> plaintext
    public_key_to_hex,            # (bytes) -> str
    public_key_from_hex,          # (str) -> bytes
)
```

### Getting the Client's Public Key

```python
pub_key_hex = server.rpc.get_public_key()  # Returns 64-char hex string
```

---

## JavaScript API Reference

### Connection Config

```javascript
const server = await connectToServer({
    server_url: "https://hypha.aicell.io",
    encryption: true,
});
```

### Service Registration

```javascript
await server.registerService({
    id: "my-service",
    config: {
        trusted_keys: ["aabbccdd..."],  // Optional
    },
    myMethod: myFunction,
});
```

### Service Discovery with Encryption

```javascript
const svc = await server.getService("my-service", {
    encryption_public_key: "aabbccdd...",  // Target's public key (hex)
});
const result = await svc.myMethod(arg1, arg2);  // Encrypted transparently
```

### Key Utilities

```javascript
import {
    generateEncryptionKeypair,  // () -> Promise<{privateKey, publicKey}>
    encryptPayload,             // (myPriv, theirPub, plaintext) -> Promise<{nonce, ciphertext}>
    decryptPayload,             // (myPriv, theirPub, nonce, ciphertext) -> Promise<plaintext>
    publicKeyToHex,             // (Uint8Array) -> string
    publicKeyFromHex,           // (string) -> Uint8Array
} from "hypha-rpc";
```

### Getting the Client's Public Key

```javascript
const pubKeyHex = server.rpc.getPublicKey();  // Returns 64-char hex string
```

---

## Usage Examples

### Example 1: Encrypted AI Model Serving

A researcher exposes an AI model as an encrypted service. Only authorized lab members can invoke it.

```python
# Model server
server = await connect_to_server({
    "server_url": "https://hypha.aicell.io",
    "encryption": True,
})

# Share server.rpc.get_public_key() with authorized users
await server.register_service({
    "id": "private-model",
    "config": {
        "trusted_keys": [
            "a1b2c3...",  # Alice's public key
            "d4e5f6...",  # Bob's public key
        ],
    },
    "predict": lambda image: model.predict(image),
})
```

```python
# Alice's client
client = await connect_to_server({
    "server_url": "https://hypha.aicell.io",
    "encryption": True,
})

model = await client.get_service("private-model",
    encryption_public_key="<model-server-pub-key>"
)
result = await model.predict(my_image)  # Encrypted end-to-end
```

### Example 2: Cross-Language Encrypted Communication

A Python service communicates with a JavaScript browser client, both using encryption.

```python
# Python service
server = await connect_to_server({
    "server_url": "https://hypha.aicell.io",
    "encryption": True,
})
pub_key = server.rpc.get_public_key()
# Share pub_key with the JS client

await server.register_service({
    "id": "secure-compute",
    "config": {
        "trusted_keys": [js_client_pub_key],
    },
    "compute": lambda data: heavy_computation(data),
})
```

```javascript
// JavaScript client (browser)
const client = await connectToServer({
    server_url: "https://hypha.aicell.io",
    encryption: true,
});
const myPubKey = client.rpc.getPublicKey();
// Share myPubKey with the Python service

const svc = await client.getService("secure-compute", {
    encryption_public_key: pythonServicePubKey,
});
const result = await svc.compute(sensitiveData);
```

---

## Security Properties

### What Is Guaranteed

| Property | Mechanism |
|----------|-----------|
| **Confidentiality** | XSalsa20 stream cipher; 256-bit key derived via Curve25519 ECDH |
| **Integrity** | Poly1305 MAC; any modification to ciphertext is detected |
| **Authentication** | `crypto_box` is a two-party authenticated construction; both sides prove knowledge of their private key |
| **Caller identity** | `trusted_keys` verifies the caller's public key after decryption |
| **Non-forgery by server** | Server does not know either private key; cannot produce valid ciphertext |

### Nonce Safety

Each message uses a **random 24-byte nonce** generated from the OS CSPRNG (`nacl.utils.random` / `nacl.randomBytes`). With a 192-bit nonce space, the probability of a collision is negligible even at billions of messages:

```
P(collision) ≈ n² / (2 × 2^192)
At 1 billion messages: P ≈ 10^(-39)
```

### Performance

| Operation | Time | Notes |
|-----------|------|-------|
| Keypair generation | ~0.1ms | Once per connection |
| Encrypt (per message) | ~0.02ms per KB | XSalsa20 is a stream cipher |
| Decrypt (per message) | ~0.02ms per KB | Same |
| Overhead vs. network latency | Negligible | Typically <1% of total RPC call time |

---

## Known Limitations

### No Forward Secrecy

The current design uses **static Curve25519 keys** for the lifetime of a connection. If a private key is compromised in the future, an attacker who recorded past encrypted traffic could decrypt it.

**Mitigation**: Reconnect periodically to rotate keys. For high-security use cases, consider wrapping Hypha traffic in a TLS tunnel with ephemeral keys (mTLS).

### No Sequence Numbering / Replay Protection

There is no explicit message sequence counter. An adversary with network access could theoretically replay a captured encrypted message. In practice, this is mitigated by:

- Random nonces (duplicate detection)
- RPC promise IDs (duplicate responses are ignored)
- Session-scoped call chains

### Routing Metadata Is Visible

The server (and any network observer with access to the decrypted TLS stream) can see:

- Who is calling whom (`from`, `to`)
- Which method is being called (`method`)
- Message sizes and timing
- Session identifiers

Only the **payload** (function arguments and return values) is encrypted.

### No Built-in PKI

Public keys are raw 32-byte values (represented as hex strings). There is no certificate authority or signing infrastructure to verify "this key belongs to service X." Key authenticity depends entirely on the out-of-band exchange mechanism.

---

## Comparison with Alternatives

| | Hypha E2E | mTLS | WireGuard | Signal Protocol |
|---|---|---|---|---|
| **Encryption** | XSalsa20-Poly1305 | AES-GCM (TLS 1.3) | ChaCha20-Poly1305 | AES-CBC + HMAC |
| **Key agreement** | Static Curve25519 | Ephemeral ECDHE | Static + ephemeral | X3DH + Double Ratchet |
| **Forward secrecy** | No | Yes | Yes | Yes |
| **Authentication** | crypto_box (mutual) | Certificates (mutual) | Pre-shared keys | Identity keys + prekeys |
| **Key exchange** | Out-of-band | PKI/CA | Config file | Key server + verification |
| **Metadata protection** | No (routing visible) | Partial (SNI) | Full tunnel | Full |
| **Implementation complexity** | ~175 LOC | Framework-level | Kernel module | High (libsignal) |
| **Dependencies** | PyNaCl / tweetnacl | OS TLS stack | Kernel | libsignal |
| **Best for** | Service RPC over trusted relay | Web/API security | Network tunnels | Messaging apps |

### When Hypha E2E Is the Right Choice

- Service-to-service RPC where the relay server should not see payloads
- Scientific computing with sensitive data (medical imaging, genomics)
- AI model serving where model inputs/outputs are confidential
- Multi-tenant environments where workspace isolation is not sufficient

### When to Use Something Else

- **Compliance requirements (HIPAA, PCI-DSS)**: Use mTLS at the transport layer (reverse proxy) in addition to E2E encryption
- **Zero-trust networks**: Use WireGuard or similar for full tunnel encryption
- **Chat/messaging applications**: Use the Signal Protocol for forward secrecy and deniability

---

## Deployment Guidance

### Minimum Security

```python
# Enable encryption — protects payloads from the server
server = await connect_to_server({
    "server_url": "wss://hypha.example.com",
    "encryption": True,
})
```

### Recommended Security

```python
# Enable encryption + restrict callers
server = await connect_to_server({
    "server_url": "wss://hypha.example.com",
    "encryption": True,
})

await server.register_service({
    "id": "sensitive-service",
    "config": {
        "trusted_keys": [
            "aabb...",  # Pre-shared authorized caller keys
        ],
    },
    "process": process_function,
})
```

### High Security (Regulated Environments)

For healthcare, finance, or government deployments, combine:

1. **mTLS** on the transport (reverse proxy with client certificates)
2. **E2E encryption** on the payload (protects against compromised relay)
3. **`trusted_keys`** for application-level caller authentication
4. **Key rotation** via periodic reconnection
5. **Audit logging** of `caller_public_key` from the context

```
Client  ──mTLS──▶  Reverse Proxy  ──▶  Hypha Server  ──▶  Client
                   (cert verified)      (can't read payload)
        ──────── E2E encrypted (crypto_box) ──────────────▶
```

---

## Future Work

These improvements are planned but not yet implemented:

| Feature | Impact | Complexity |
|---------|--------|------------|
| **Ephemeral key exchange** | Adds forward secrecy (biggest single improvement) | Medium |
| **Key rotation protocol** | Rotate keys without reconnecting | Medium |
| **Associated Authenticated Data (AAD)** | Bind routing metadata to encrypted payload | Low |
| **Replay protection** | Sequence numbers per peer | Low |
| **Group encryption** | Efficient broadcast to multiple recipients | High |
| **Key pinning / TOFU** | Trust-on-first-use with key persistence | Medium |
| **Audit logging** | Structured logs of all encryption events | Low |

---

## References

- Bernstein, D.J. (2006). "Curve25519: New Diffie-Hellman Speed Records." [CR 2006/024](https://cr.yp.to/ecdh.html)
- Bernstein, D.J. (2008). "The Salsa20 family of stream ciphers." [CR](https://cr.yp.to/salsa20.html)
- Bernstein, D.J. (2005). "The Poly1305-AES message-authentication code." [CR 2005/025](https://cr.yp.to/mac.html)
- PyNaCl documentation: https://pynacl.readthedocs.io/
- tweetnacl.js: https://tweetnacl.js.org/
- libsodium documentation: https://doc.libsodium.org/
- NaCl: Networking and Cryptography library: https://nacl.cr.yp.to/
