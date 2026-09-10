# network_os wire protocol — language-agnostic spec

Any language with SHA-256, Ed25519, X25519, HKDF-SHA256, and AES-256-GCM
(i.e. essentially every modern language) can implement a compatible node.
Nothing here is Python-specific; this doc is the contract, `network_os.py`
is just the reference implementation.

## Transport

Plain TCP. One JSON object per line (`\n`-terminated), UTF-8 encoded.

## Message types

### `hello` / `hello_ack` — plaintext, signed (not encrypted)

```json
{
  "type": "hello",
  "node_id": "<16 lowercase hex chars, sha256(identity_label)[:16] or any stable id>",
  "listen_port": 8765,
  "strand_hex": "<64 hex chars — your identity strand, or 64 zero chars if you have none>",
  "signing_pub": "<64 hex chars — raw 32-byte Ed25519 public key>",
  "exchange_pub": "<64 hex chars — raw 32-byte X25519 public key>",
  "sig": "<128 hex chars — raw 64-byte Ed25519 signature>"
}
```

`sig` = Ed25519 sign over the UTF-8 bytes of exactly:
```
f"{node_id}|{strand_hex}|{exchange_pub}"
```
(pipe-separated, no extra whitespace). `hello_ack` has the identical shape,
sent back once a `hello`'s signature verifies.

Why plaintext: these fields aren't secret (public keys, a node id) — the
signature is what matters, proving the sender holds the matching private
key. This is the same reason a TLS ClientHello or an SSH host key isn't
encrypted either.

### Session key derivation (both sides compute independently, nothing sent)

```
shared_secret = X25519_ECDH(my_exchange_priv, their_exchange_pub)
session_key   = HKDF-SHA256(
                   ikm  = shared_secret,
                   salt = 32 zero bytes,          # RFC 5869 default when salt is omitted
                   info = b"network-os-session",
                   length = 32
                 )
```

### `enc` — every message after the handshake (block, ping, pong)

```json
{"type": "enc", "node_id": "<sender's node_id>", "payload": "<hex>"}
```

`payload` = hex(`nonce || ciphertext || tag`) where:
- `nonce` = 12 random bytes
- AES-256-GCM under `session_key`
- AAD = UTF-8 bytes of the **sender's own** `node_id`
- `ciphertext || tag`: 16-byte GCM tag appended directly after the
  ciphertext (the standard AEAD-interface layout — this is what
  `cryptography`'s `AESGCM.encrypt()` returns and what you get if you
  concatenate your library's ciphertext + auth tag manually).

Decrypted plaintext is UTF-8 JSON, one of:

```json
{"type": "ping"}
{"type": "pong"}
{"type": "block", "block": { ... , "sig": "<128 hex>"}}
```

### Block signing

`sig` inside a block is an Ed25519 signature over the canonical JSON
serialization of the block **with the `sig` field removed**, keys sorted
alphabetically, in the exact style `json.dumps(block, sort_keys=True)`
produces:

```
{"block_id": "...", "confidence": 1, "feature_hash": "...", "source": "...", "timestamp": 1234567890}
```

i.e. `", "` between fields, `": "` between key and value, double-quoted
string values, bare digits for integers. **Use integers, not floats, for
any numeric field you sign** — floating-point string formatting differs
across languages (`0.9` vs `0.900000` vs locale decimal separators) and
will break signature verification even when the numeric value is
"the same." Timestamps as whole Unix seconds, confidence as an integer
0-100 (or similar), are both safer choices than a float for cross-language
blocks.

The receiver reconstructs this canonical string from its own parsed copy
of the block (not from your original transmitted bytes) and verifies
against it — so you don't need to match anyone else's JSON whitespace
when *sending*, only when *signing*.

## What this gets you

A node in any language that speaks this protocol can: complete a mutually
authenticated handshake with a Python node, derive the same session key,
and send it a validly signed block that lands in its `network_ledger`,
gets DAS-scored, and shows up in its MOS rollup — exactly as demonstrated
by `interop_client.js` in this same delivery, which is a real Node.js
client doing exactly this against a running `network_os.py` node.
