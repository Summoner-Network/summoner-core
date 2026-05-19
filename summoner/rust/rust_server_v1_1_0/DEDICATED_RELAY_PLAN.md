# Dedicated Relay Plan

## Goal

Update `rust_server_v1_1_0` so the relay can run in dedicated mode, routing by
verified identity fingerprints instead of always broadcasting every message to
every connected client.

Current behavior:

```text
client A sends line
  -> server wraps as {"remote_addr": A, "content": line}
  -> server sends to every connected client except A
```

Target behavior when dedicated relay mode is enabled:

```text
client A sends signed identity envelope
  -> server parses JSON
  -> extracts sender identity, receiver identity, signature, and signing key
  -> validates sender public identity
  -> validates envelope signature
  -> computes sender_fp and receiver_fp
  -> checks server access allowlist
  -> checks directed graph sender_fp -> receiver_fp
  -> sends only to connected clients bound to receiver_fp
```

## Adapter Boundary

The relay should use generic naming in config and internal routing concepts:

- sender identity
- receiver identity
- identity fingerprint
- signed envelope
- validation adapter

Today the only supported validation adapter is Aurora, so the first
implementation must verify Aurora identity records and Aurora envelope
signatures exactly. The config and relay model should not permanently bake
Aurora terms into field names, because future adapters may use a different wire
shape while keeping the same dedicated relay policy model.

## Important Interpretation

The requirement says:

> a list of fp for accessing the server. Any payload that does contain an
> identity witht a fingerprint included in the list is directly rejected

I interpret this as a typo and plan to implement:

> Any payload that does **not** contain a valid identity with a fingerprint in
> the allowlist is rejected before relay.

## Files To Touch

| File | Purpose |
| --- | --- |
| `src/config/mod.rs` | Add config structs and Python-dict parsing for dedicated relay mode. |
| `src/server/mod.rs` | Replace broadcast path with validate-and-route path when enabled. |
| `src/server/dedicated_relay.rs` | New module for path extraction, fingerprinting, signature validation, and route decisions. |
| `src/server/backpressure.rs` | Reuse or extend backpressure commands for repeated invalid payloads. |
| `Cargo.toml` | Add crypto, hash, and base64 dependencies. |
| `templates/server_config.json` | Add an example config section under `hyper_parameters`. |

## Proposed Config Shape

`summoner/server/server.py` flattens `hyper_parameters` before passing config to
Rust, so this belongs under `hyper_parameters.dedicated_relay` in JSON.

```json
{
  "hyper_parameters": {
    "dedicated_relay": {
      "enabled": true,
      "validation_adapter": "aurora",

      "allowed_fingerprints": [
        "fp_sender_abc123",
        "fp_receiver_def456"
      ],

      "communication_graph": {
        "fp_sender_abc123": ["fp_receiver_def456"],
        "fp_receiver_def456": ["fp_sender_abc123"]
      },

      "paths": {
        "envelope_root": "",
        "sender_identity": "from",
        "receiver_identity": "to",
        "envelope_signature": "sig",
        "sender_signing_key": "from.pub_sig_b64",
        "sender_identity_signature": "from.sig"
      },

      "policy": {
        "allow_null_receiver_binding": true,
        "reject_unknown_receiver": true,
        "reject_when_receiver_disconnected": false
      },

      "invalid_attempts": {
        "enabled": true,
        "window_secs": 60,
        "throttle_threshold": 3,
        "flow_control_threshold": 6,
        "disconnect_threshold": 10
      }
    }
  }
}
```

## Config Semantics

| Field | Meaning |
| --- | --- |
| `enabled` | If `false`, preserve current broadcast behavior. |
| `validation_adapter` | Identity and signature validation adapter. Default and only current value: `aurora`. |
| `allowed_fingerprints` | Sender must be in this list. Receiver should also be in this list when a receiver identity exists. |
| `communication_graph` | Directed routing graph. A message from `A` to `B` is allowed only if `B` is in `communication_graph[A]`. |
| `paths.envelope_root` | Optional dot path to the envelope object when the envelope is nested. Empty string means the top-level JSON object. |
| `paths.sender_identity` | Dot path to the sender identity object. Default for Aurora: `from`. |
| `paths.receiver_identity` | Dot path to the receiver identity object. Default for Aurora: `to`. |
| `paths.envelope_signature` | Dot path to envelope signature. Default: `sig`. |
| `paths.sender_signing_key` | Dot path to Ed25519 public signing key. Default: `from.pub_sig_b64`. |
| `paths.sender_identity_signature` | Dot path to sender identity self-signature. Default: `from.sig`. |
| `allow_null_receiver_binding` | Allows a signed message with no receiver identity to bind a socket to `sender_fp`, without broadcasting it. For Aurora, this covers `to: null` discovery. |
| `reject_unknown_receiver` | Reject if `receiver_fp` is not in `allowed_fingerprints`. |
| `reject_when_receiver_disconnected` | Reject valid routed messages when no connected client is currently bound to `receiver_fp`. |

## Routing Diagram

```text
                 incoming line
                      |
                      v
             parse as JSON object
                      |
                      v
          dedicated_relay.enabled?
              |                 |
            false              true
              |                 |
              v                 v
       current broadcast     extract configured paths
                                |
                                v
                        verify sender identity
                                |
                                v
                       verify envelope signature
                                |
                                v
                         compute sender_fp
                                |
                                v
                     sender in allowlist?
                                |
                                v
                      bind socket -> sender_fp
                                |
                +---------------+---------------+
                |                               |
        receiver is null                 receiver identity
                |                               |
                v                               v
       registration only, no relay        compute receiver_fp
                                                |
                                                v
                              graph allows sender -> receiver?
                                                |
                                                v
                                  relay only to receiver_fp
```

## Implementation Plan

### 1. Add Config Types

Add these types in `src/config/mod.rs`.

```rust
use std::collections::{HashMap, HashSet};

#[derive(Debug, Clone)]
pub struct DedicatedRelayConfig {
    pub enabled: bool,
    pub validation_adapter: String,
    pub allowed_fingerprints: HashSet<String>,
    pub communication_graph: HashMap<String, HashSet<String>>,
    pub paths: DedicatedRelayPaths,
    pub policy: DedicatedRelayPolicy,
    pub invalid_attempts: InvalidAttemptPolicy,
}

#[derive(Debug, Clone)]
pub struct DedicatedRelayPaths {
    pub envelope_root: String,
    pub sender_identity: String,
    pub receiver_identity: String,
    pub envelope_signature: String,
    pub sender_signing_key: String,
    pub sender_identity_signature: String,
}

#[derive(Debug, Clone)]
pub struct DedicatedRelayPolicy {
    pub allow_null_receiver_binding: bool,
    pub reject_unknown_receiver: bool,
    pub reject_when_receiver_disconnected: bool,
}

#[derive(Debug, Clone)]
pub struct InvalidAttemptPolicy {
    pub enabled: bool,
    pub window_secs: u64,
    pub throttle_threshold: usize,
    pub flow_control_threshold: usize,
    pub disconnect_threshold: usize,
}
```

Then add this field to `ServerConfig`:

```rust
pub dedicated_relay: DedicatedRelayConfig,
```

### 2. Add Dedicated Relay Module

Create `src/server/dedicated_relay.rs`.

Responsibilities:

- Extract nested values by dot path.
- Dispatch to the configured validation adapter.
- Validate sender and receiver public identities.
- Validate envelope signatures.
- Compute identity fingerprints.
- Return a routing decision.

The first adapter is `aurora`; later adapters should implement the same
decision contract without changing the relay policy vocabulary.

```rust
pub enum RouteDecision {
    LegacyBroadcast,
    BindOnly {
        sender_fp: String,
    },
    Directed {
        sender_fp: String,
        receiver_fp: String,
    },
    Reject {
        reason: String,
    },
}
```

### 3. Current Adapter: Aurora Fingerprints

The current `aurora` adapter computes fingerprints from `pub_sig_b64`.

```text
raw = base64_decode(pub_sig_b64)
digest = sha256(raw)
fingerprint = base64url_no_padding(digest)[0..22]
```

Rust helper:

```rust
fn id_fingerprint(pub_sig_b64: &str) -> Result<String, RouteReject> {
    let raw = base64_decode(pub_sig_b64)?;
    let digest = Sha256::digest(&raw);
    let encoded = base64_urlsafe_no_pad(digest);
    Ok(encoded.chars().take(22).collect())
}
```

### 4. Current Adapter: Aurora Canonical JSON

The current `aurora` adapter signs compact JSON with sorted keys. Rust must
recursively sort object keys before serializing.

```rust
fn canonical_json_bytes(value: &JsonValue) -> Result<Vec<u8>, RouteReject> {
    let sorted = sort_json_value(value);
    serde_json::to_vec(&sorted).map_err(|_| RouteReject::CanonicalJson)
}
```

### 5. Current Adapter: Verify Public Identity

For the current `aurora` adapter, this is equivalent to Python
`verify_public_id(pub)`.

Signed identity core:

```json
{
  "created_at": "...",
  "pub_enc_b64": "...",
  "pub_sig_b64": "...",
  "meta": {}
}
```

`meta` is included only when present and non-null.

```rust
fn verify_public_id(public_id: &JsonValue) -> Result<(), RouteReject> {
    ensure(public_id["v"] == "id.v1")?;
    let core = build_public_id_core(public_id)?;
    verify_ed25519(
        public_id["pub_sig_b64"].as_str()?,
        canonical_json_bytes(&core)?,
        public_id["sig"].as_str()?,
    )
}
```

### 6. Current Adapter: Verify Envelope Signature

For the current `aurora` adapter, this is equivalent to the signature created by
Aurora `seal_envelope`.

The configured paths locate the sender and receiver identity values. The
adapter still builds the canonical signed core using Aurora's wire-native key
names, because that is what the sender signed.

Signed envelope core:

```json
{
  "v": "env.v1",
  "payload": "...",
  "session_proof": {},
  "from": {},
  "to": {}
}
```

```rust
fn verify_envelope_signature(
    root: &JsonValue,
    sender_identity: &JsonValue,
    receiver_identity: &JsonValue,
) -> Result<(), RouteReject> {
    let core = json!({
        "v": root.get("v").ok_or(RouteReject::MissingEnvelopeVersion)?,
        "payload": root.get("payload").ok_or(RouteReject::MissingPayload)?,
        "session_proof": root.get("session_proof").ok_or(RouteReject::MissingSessionProof)?,
        "from": sender_identity,
        "to": receiver_identity,
    });

    verify_ed25519(
        sender_identity["pub_sig_b64"].as_str().ok_or(RouteReject::MissingSigningKey)?,
        canonical_json_bytes(&core)?,
        root["sig"].as_str().ok_or(RouteReject::MissingEnvelopeSignature)?,
    )
}
```

### 7. Bind Sockets To Fingerprints

Extend `Client`.

```rust
#[derive(Clone)]
pub struct Client {
    pub addr: SocketAddr,
    pub writer: Arc<Mutex<tokio::net::tcp::OwnedWriteHalf>>,
    pub control_tx: mpsc::Sender<ClientCommand>,
    pub verified_identity_fp: Arc<RwLock<Option<String>>>,
}
```

After a valid signed payload:

```rust
*sender.verified_identity_fp.write().await = Some(sender_fp.clone());
```

### 8. Replace Broadcast Decision Point

Current code path:

```rust
broadcast_message(clients, sender, &envelope_text, queue_tx, logger).await;
```

New code path:

```rust
match route_decision {
    RouteDecision::LegacyBroadcast => {
        broadcast_message(clients, sender, &envelope_text, queue_tx, logger).await;
    }
    RouteDecision::BindOnly { sender_fp } => {
        bind_client_identity(sender, sender_fp).await;
        logger.info("identity bound; no relay performed");
    }
    RouteDecision::Directed { sender_fp, receiver_fp } => {
        bind_client_identity(sender, sender_fp).await;
        route_message_to_fingerprint(
            clients,
            sender,
            &receiver_fp,
            &envelope_text,
            queue_tx,
            logger,
        ).await;
    }
    RouteDecision::Reject { reason } => {
        record_invalid_attempt(sender.addr, reason, config, logger).await;
    }
}
```

### 9. Add Directed Delivery

```rust
async fn route_message_to_fingerprint(
    clients: &ClientList,
    sender: &Client,
    receiver_fp: &str,
    msg: &str,
    queue_tx: mpsc::Sender<usize>,
    logger: &Logger,
) {
    let snapshot = {
        let guard = clients.read().await;
        let mut out = Vec::new();

        for client in guard.iter() {
            if client.addr == sender.addr {
                continue;
            }

            let fp = client.verified_identity_fp.read().await.clone();
            if fp.as_deref() == Some(receiver_fp) {
                out.push(client.clone());
            }
        }

        out
    };

    let _ = queue_tx.try_send(snapshot.len());
    write_to_snapshot(snapshot, msg, logger).await;
}
```

## Backpressure For Invalid Attempts

Add a small tracker for invalid payloads. This is separate from queue-size
backpressure.

| Attempts Within Window | Action |
| --- | --- |
| `< throttle_threshold` | Reject and log only. |
| `>= throttle_threshold` | Send throttle command or sleep before processing. |
| `>= flow_control_threshold` | Send flow-control command or longer sleep. |
| `>= disconnect_threshold` | Disconnect and optionally quarantine. |

Initial type:

```rust
struct InvalidAttemptTracker {
    by_addr: HashMap<SocketAddr, AttemptWindow>,
}

struct AttemptWindow {
    started_at: Instant,
    count: usize,
}
```

Later improvement: key by IP address instead of full `SocketAddr`, so reconnects
from a different port still count.

## Abuse Control And Memory Strategy

Dedicated relay mode reduces accidental fanout, but it is not a complete DDoS
defense by itself. The relay still needs layered controls:

1. Drop bad payloads before allocation-heavy work.
2. Track invalid attempts by IP address, not only by `SocketAddr`, because a
   reconnect may use a new source port.
3. Apply backpressure thresholds to invalid attempts.
4. Bound all in-memory maps with TTL cleanup.
5. Keep OS/firewall blocking as an optional outer layer rather than the default
   in-process behavior.

### Fast Rejection Path

The routing validator should reject in this order:

| Stage | Check | Why It Comes Early |
| --- | --- | --- |
| Frame size | Reject lines above `max_frame_bytes`. | Prevents memory pressure from giant payloads. |
| JSON parse | Reject malformed JSON. | Required before path extraction. |
| Required paths | Reject missing sender identity, signature, or signing key. | Avoids crypto work when shape is wrong. |
| Sender fingerprint | Compute and allowlist-check sender. | Blocks unknown identities before graph lookup. |
| Public identity signature | Verify sender identity self-signature. | Proves the sender identity record is internally valid. |
| Envelope signature | Verify signed envelope core. | Proves payload, session proof, sender identity, and receiver identity were signed by sender. |
| Directed graph | Check `sender_fp -> receiver_fp`. | Prevents broadcast and unauthorized pairings. |

### Invalid Attempt Tracker

Track repeated invalid payloads separately from normal queue backpressure.

```rust
struct InvalidAttemptTracker {
    by_ip: HashMap<IpAddr, AttemptWindow>,
    max_entries: usize,
}

struct AttemptWindow {
    started_at: Instant,
    last_seen: Instant,
    count: usize,
    last_reason: String,
}
```

Use IP-level keys for abuse controls:

```text
SocketAddr = 203.0.113.7:53144
IpAddr     = 203.0.113.7
```

`SocketAddr` is useful for active client tasks. `IpAddr` is better for repeated
bad reconnects.

### Suggested Invalid Attempt Policy

```json
{
  "invalid_attempts": {
    "enabled": true,
    "window_secs": 60,
    "max_entries": 10000,
    "throttle_threshold": 3,
    "flow_control_threshold": 6,
    "disconnect_threshold": 10,
    "quarantine_threshold": 12,
    "quarantine_secs": 600
  }
}
```

| Threshold | Action |
| --- | --- |
| `throttle_threshold` | Delay processing for that client. |
| `flow_control_threshold` | Longer delay and stop reading temporarily. |
| `disconnect_threshold` | Disconnect the current socket. |
| `quarantine_threshold` | Temporarily reject future connections from the same IP. |

### Ghost And Past Connections

The server already removes clients from `ClientList` when their task exits.
Dedicated routing should also make identity binding self-cleaning:

- Store identity binding on the `Client`; when the client is removed, the
  binding disappears with it.
- If we add reverse indexes like `fp -> clients`, store weak or address-based
  entries and clean them whenever a client exits.
- Run periodic cleanup for:
  - stale quarantine entries,
  - expired invalid-attempt windows,
  - disconnected identity index entries,
  - old route metrics.

Memory should be bounded:

| Structure | Bound |
| --- | --- |
| `ClientList` | Number of active TCP connections. |
| invalid attempts | `max_entries` plus TTL cleanup. |
| quarantine list | TTL cleanup plus optional `max_entries`. |
| identity bindings | Attached to active clients only. |
| route graph | Static config, loaded once. |

If `InvalidAttemptTracker` reaches `max_entries`, evict expired windows first,
then evict oldest `last_seen` entries. This prevents an attacker from growing
memory without bound by rotating IPs.

## Firewall Integration

Application-level rejection is the first line of defense because it understands
identity fingerprints and signatures. OS firewall blocking is a stronger outer
layer because it can drop traffic before the relay does JSON parsing or crypto.

Recommended layering:

```text
internet / LAN
  -> OS firewall / cloud firewall / load balancer
  -> TCP accept limits
  -> frame-size limits
  -> JSON/path validation
  -> identity/signature validation
  -> graph routing
```

### Can Rust Run `iptables`?

Yes. Rust can spawn system commands with `std::process::Command`, including
`iptables`, but the relay should not do this by default.

Reasons:

- `iptables` usually requires root or `CAP_NET_ADMIN`.
- Many Linux systems now use `nftables` directly or have `iptables` as a
  compatibility frontend.
- A bug in application logic could lock out legitimate clients.
- Shelling out from a network-facing process increases operational risk.
- macOS and Windows do not use `iptables`.

If we support this, make it explicitly opt-in and command-template based, with
no shell interpolation:

```json
{
  "firewall": {
    "enabled": false,
    "mode": "external_command",
    "backend": "iptables",
    "threshold": 20,
    "cooldown_secs": 3600,
    "command": ["iptables", "-I", "INPUT", "-s", "{ip}", "-j", "DROP"]
  }
}
```

Implementation sketch:

```rust
use std::process::Command;

fn block_ip_with_command(template: &[String], ip: IpAddr) -> std::io::Result<()> {
    if template.is_empty() {
        return Ok(());
    }

    let program = &template[0];
    let args = template[1..]
        .iter()
        .map(|part| part.replace("{ip}", &ip.to_string()))
        .collect::<Vec<_>>();

    let status = Command::new(program).args(args).status()?;
    if !status.success() {
        // log failure; do not crash the relay
    }
    Ok(())
}
```

Important guardrails:

- Never invoke through a shell.
- Only replace a validated `{ip}` placeholder.
- Require `firewall.enabled = true`.
- Rate-limit firewall command execution.
- Keep a local record of firewall actions so cleanup/unblock is possible.
- Prefer an external supervisor, firewall daemon, cloud firewall, or fail2ban
  style integration for production.

## Required Dependencies

Likely additions:

```toml
base64 = "0.22"
sha2 = "0.10"
ed25519-dalek = "2"
```

`serde_json` is already present.

## Test Plan

| Test | Expected Result |
| --- | --- |
| Dedicated routing disabled | Existing broadcast behavior remains unchanged. |
| Malformed JSON | Rejected, not relayed, invalid attempt recorded. |
| Missing sender identity | Rejected. |
| Invalid public identity self-signature | Rejected. |
| Invalid envelope signature | Rejected. |
| Valid sender not in allowlist | Rejected. |
| Valid sender allowed but receiver not allowed | Rejected. |
| Valid identities but graph lacks edge | Rejected. |
| Valid `sender -> receiver` edge | Delivered only to clients bound to `receiver_fp`. |
| Valid null-receiver binding message | Sender socket bound to `sender_fp`, no message relayed. |
| Repeated invalid payloads | Throttle, flow-control, and disconnect thresholds activate. |

## Implementation Order

1. Add config structs and parsing defaults.
2. Add `dedicated_relay.rs` with path extraction, fingerprinting, canonical JSON, and signature verification.
3. Add `verified_identity_fp` binding to `Client`.
4. Split current `broadcast_message` into reusable snapshot and write helpers.
5. Add directed relay path.
6. Add invalid attempt tracking and backpressure integration.
7. Update `templates/server_config.json`.
8. Add Rust unit tests for validation and routing decisions.
9. Run Rust tests and build for `rust_server_v1_1_0`.
10. Smoke test with two current Aurora-backed agents using known fingerprints and a small directed graph.
