// interop_client.js
// A real Node.js client speaking the same wire protocol as network_os.py
// (see PROTOCOL.md). Connects to a running Python node, completes a
// mutually-authenticated handshake (Ed25519 + X25519 + HKDF-SHA256),
// and sends one signed, AES-256-GCM-encrypted block.
//
// Usage: node interop_client.js <host> <port>

const net = require('net');
const crypto = require('crypto');

function b64urlToBuf(b64url) {
  return Buffer.from(b64url.replace(/-/g, '+').replace(/_/g, '/'), 'base64');
}
function bufToB64url(buf) {
  return buf.toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function rawPubFromKeyObject(pubKeyObject) {
  const jwk = pubKeyObject.export({ format: 'jwk' });
  return b64urlToBuf(jwk.x);
}

function ed25519PubFromRawHex(hex) {
  const raw = Buffer.from(hex, 'hex');
  const jwk = { kty: 'OKP', crv: 'Ed25519', x: bufToB64url(raw) };
  return crypto.createPublicKey({ key: jwk, format: 'jwk' });
}

function x25519PubFromRawHex(hex) {
  const raw = Buffer.from(hex, 'hex');
  const jwk = { kty: 'OKP', crv: 'X25519', x: bufToB64url(raw) };
  return crypto.createPublicKey({ key: jwk, format: 'jwk' });
}

// Canonical string matching Python's json.dumps(block, sort_keys=True)
// for our fixed field set — see PROTOCOL.md. Only str/int values.
function canonicalBlockString(block) {
  const keys = Object.keys(block).sort();
  const parts = keys.map((k) => {
    const v = block[k];
    const vStr = typeof v === 'number' ? String(v) : JSON.stringify(v);
    return `${JSON.stringify(k)}: ${vStr}`;
  });
  return `{${parts.join(', ')}}`;
}

async function main() {
  const host = process.argv[2] || '127.0.0.1';
  const port = parseInt(process.argv[3] || '9501', 10);

  const nodeId = crypto.createHash('sha256').update('js-interop-node').digest('hex').slice(0, 16);
  const strandHex = '0'.repeat(64); // no digital-DNA identity on this side — that's fine, see PROTOCOL.md

  const signingKeys = crypto.generateKeyPairSync('ed25519');
  const exchangeKeys = crypto.generateKeyPairSync('x25519');
  const mySigningPubHex = rawPubFromKeyObject(signingKeys.publicKey).toString('hex');
  const myExchangePubHex = rawPubFromKeyObject(exchangeKeys.publicKey).toString('hex');

  const toSign = `${nodeId}|${strandHex}|${myExchangePubHex}`;
  const sig = crypto.sign(null, Buffer.from(toSign), signingKeys.privateKey);

  const hello = {
    type: 'hello',
    node_id: nodeId,
    listen_port: 0,
    strand_hex: strandHex,
    signing_pub: mySigningPubHex,
    exchange_pub: myExchangePubHex,
    sig: sig.toString('hex'),
  };

  const socket = net.createConnection(port, host, () => {
    console.log(`[js] connected to ${host}:${port}, sending hello (node_id=${nodeId})`);
    socket.write(JSON.stringify(hello) + '\n');
  });

  let buffer = '';
  socket.on('data', (chunk) => {
    buffer += chunk.toString('utf8');
    let idx;
    while ((idx = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 1);
      if (line.trim()) handleLine(JSON.parse(line));
    }
  });

  function handleLine(msg) {
    if (msg.type === 'hello_ack') {
      console.log('[js] received hello_ack from Python node, verifying its signature...');
      const peerSigningPub = ed25519PubFromRawHex(msg.signing_pub);
      const toVerify = `${msg.node_id}|${msg.strand_hex}|${msg.exchange_pub}`;
      const ok = crypto.verify(null, Buffer.from(toVerify), peerSigningPub, Buffer.from(msg.sig, 'hex'));
      if (!ok) {
        console.error('[js] Python node signature INVALID — aborting');
        socket.end();
        return;
      }
      console.log('[js] Python node signature verified OK');

      const peerExchangePub = x25519PubFromRawHex(msg.exchange_pub);
      const sharedSecret = crypto.diffieHellman({
        privateKey: exchangeKeys.privateKey,
        publicKey: peerExchangePub,
      });
      const sessionKey = Buffer.from(
        crypto.hkdfSync('sha256', sharedSecret, Buffer.alloc(32), Buffer.from('network-os-session'), 32)
      );
      console.log('[js] derived session key via X25519 + HKDF-SHA256:', sessionKey.toString('hex'));

      sendSignedBlock(sessionKey);
    }
  }

  function sendSignedBlock(sessionKey) {
    const featureHash = crypto.createHash('sha256').update('js-originated-event').digest('hex');
    const block = {
      block_id: 'js-000001',
      source: 'js_interop_node',
      feature_hash: featureHash,
      confidence: 1,
      timestamp: Math.floor(Date.now() / 1000),
    };
    const canonical = canonicalBlockString(block);
    const blockSig = crypto.sign(null, Buffer.from(canonical), signingKeys.privateKey);
    const signedBlock = { ...block, sig: blockSig.toString('hex') };

    const innerPlaintext = Buffer.from(JSON.stringify({ type: 'block', block: signedBlock }), 'utf8');
    const nonce = crypto.randomBytes(12);
    const cipher = crypto.createCipheriv('aes-256-gcm', sessionKey, nonce, { authTagLength: 16 });
    cipher.setAAD(Buffer.from(nodeId, 'utf8')); // AAD = sender's own node_id, matching PROTOCOL.md
    const ct = Buffer.concat([cipher.update(innerPlaintext), cipher.final()]);
    const tag = cipher.getAuthTag();
    const payload = Buffer.concat([nonce, ct, tag]).toString('hex');

    const envelope = { type: 'enc', node_id: nodeId, payload };
    socket.write(JSON.stringify(envelope) + '\n');
    console.log('[js] sent signed, AES-256-GCM-encrypted block to Python node');

    setTimeout(() => {
      console.log('[js] done, closing');
      socket.end();
      process.exit(0);
    }, 300);
  }

  socket.on('error', (err) => {
    console.error('[js] socket error:', err.message);
    process.exit(1);
  });
}

main();
