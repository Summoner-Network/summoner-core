use std::collections::HashMap;
use std::fmt::Write as _;
use std::net::IpAddr;
use std::time::{Duration, Instant};

use base64::{
    Engine as _,
    engine::general_purpose::{STANDARD, URL_SAFE_NO_PAD},
};
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use serde_json::{Map, Value as JsonValue, json};
use sha2::{Digest, Sha256};

use crate::config::{DedicatedRelayConfig, InvalidAttemptPolicy};

#[derive(Debug, Clone)]
pub enum RouteDecision {
    LegacyBroadcast,
    BindOnly {
        sender_fp: String,
    },
    Directed {
        sender_fp: String,
        receiver_fp: String,
    },
    GraphFanout {
        sender_fp: String,
        receiver_fps: Vec<String>,
    },
    Reject {
        reason: String,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InvalidAttemptAction {
    LogOnly,
    Throttle,
    FlowControl,
    Disconnect,
    Quarantine,
}

#[derive(Debug)]
struct AttemptWindow {
    started_at: Instant,
    last_seen: Instant,
    count: usize,
    last_reason: String,
}

#[derive(Debug)]
pub struct InvalidAttemptTracker {
    by_ip: HashMap<IpAddr, AttemptWindow>,
}

impl InvalidAttemptTracker {
    pub fn new() -> Self {
        Self {
            by_ip: HashMap::new(),
        }
    }

    pub fn record(
        &mut self,
        ip: IpAddr,
        reason: &str,
        policy: &InvalidAttemptPolicy,
    ) -> InvalidAttemptAction {
        if !policy.enabled {
            return InvalidAttemptAction::LogOnly;
        }

        let now = Instant::now();
        let window = Duration::from_secs(policy.window_secs.max(1));
        self.cleanup_expired(now, window);
        self.enforce_max_entries(policy.max_entries.max(1));

        let entry = self.by_ip.entry(ip).or_insert_with(|| AttemptWindow {
            started_at: now,
            last_seen: now,
            count: 0,
            last_reason: String::new(),
        });

        if now.duration_since(entry.started_at) >= window {
            entry.started_at = now;
            entry.count = 0;
        }

        entry.last_seen = now;
        entry.count = entry.count.saturating_add(1);
        entry.last_reason = reason.to_string();

        if entry.count >= policy.quarantine_threshold {
            InvalidAttemptAction::Quarantine
        } else if entry.count >= policy.disconnect_threshold {
            InvalidAttemptAction::Disconnect
        } else if entry.count >= policy.flow_control_threshold {
            InvalidAttemptAction::FlowControl
        } else if entry.count >= policy.throttle_threshold {
            InvalidAttemptAction::Throttle
        } else {
            InvalidAttemptAction::LogOnly
        }
    }

    fn cleanup_expired(&mut self, now: Instant, window: Duration) {
        self.by_ip
            .retain(|_, attempt| now.duration_since(attempt.last_seen) < window);
    }

    fn enforce_max_entries(&mut self, max_entries: usize) {
        if self.by_ip.len() < max_entries {
            return;
        }
        let mut oldest = self
            .by_ip
            .iter()
            .map(|(ip, attempt)| (*ip, attempt.last_seen))
            .collect::<Vec<_>>();
        oldest.sort_by_key(|(_, last_seen)| *last_seen);
        let drop_count = self
            .by_ip
            .len()
            .saturating_sub(max_entries)
            .saturating_add(1);
        for (ip, _) in oldest.into_iter().take(drop_count) {
            self.by_ip.remove(&ip);
        }
    }
}

pub fn decide_route(content: &str, config: &DedicatedRelayConfig) -> RouteDecision {
    if !config.enabled {
        return RouteDecision::LegacyBroadcast;
    }

    if content.len() > config.max_frame_bytes {
        return reject("frame_too_large");
    }

    if config.validation_adapter.trim().to_ascii_lowercase() != "aurora" {
        return reject("unsupported_validation_adapter");
    }

    let parsed = match serde_json::from_str::<JsonValue>(content) {
        Ok(value) => value,
        Err(_) => return reject("invalid_json"),
    };

    decide_aurora_route(&parsed, config)
}

fn decide_aurora_route(value: &JsonValue, config: &DedicatedRelayConfig) -> RouteDecision {
    let root = match get_path(value, &config.paths.envelope_root) {
        Some(root) => root,
        None => return reject("missing_envelope_root"),
    };

    let sender_identity = match get_path(root, &config.paths.sender_identity) {
        Some(value) if value.is_object() => value,
        Some(_) => return reject("invalid_sender_identity"),
        None => return reject("missing_sender_identity"),
    };

    let receiver_identity = match get_path(root, &config.paths.receiver_identity) {
        Some(value) => value,
        None => return reject("missing_receiver_identity"),
    };

    let envelope_signature =
        match get_path(root, &config.paths.envelope_signature).and_then(JsonValue::as_str) {
            Some(value) if !value.is_empty() => value,
            _ => return reject("missing_envelope_signature"),
        };

    let sender_signing_key =
        match get_path(root, &config.paths.sender_signing_key).and_then(JsonValue::as_str) {
            Some(value) if !value.is_empty() => value,
            _ => return reject("missing_sender_signing_key"),
        };

    let sender_identity_signature =
        match get_path(root, &config.paths.sender_identity_signature).and_then(JsonValue::as_str) {
            Some(value) if !value.is_empty() => value,
            _ => return reject("missing_sender_identity_signature"),
        };

    let sender_fp = match id_fingerprint(sender_signing_key) {
        Ok(fp) => fp,
        Err(reason) => return reject(reason),
    };

    if !config.allowed_fingerprints.contains(&sender_fp) {
        return reject("sender_not_allowed");
    }

    if let Err(reason) = verify_public_id_with_signature(
        sender_identity,
        sender_signing_key,
        sender_identity_signature,
    ) {
        return reject(reason);
    }

    if let Err(reason) = verify_envelope_signature(
        root,
        sender_identity,
        receiver_identity,
        sender_signing_key,
        envelope_signature,
    ) {
        return reject(reason);
    }

    if receiver_identity.is_null() {
        if !config.policy.allow_receiverless_binding {
            return reject("receiverless_message_rejected");
        }
        if config.policy.route_receiverless_to_graph {
            let receiver_fps = graph_receivers(config, &sender_fp);
            if !receiver_fps.is_empty() {
                return RouteDecision::GraphFanout {
                    sender_fp,
                    receiver_fps,
                };
            }
        }
        return RouteDecision::BindOnly { sender_fp };
    }

    if let Some(receiver_fp) = receiver_identity.as_str().map(str::trim) {
        if receiver_fp.is_empty() {
            return reject("invalid_receiver_fingerprint");
        }
        return directed_route(config, sender_fp, receiver_fp.to_string());
    }

    if !receiver_identity.is_object() {
        return reject("invalid_receiver_identity");
    }

    if let Err(reason) = verify_public_id(receiver_identity) {
        return reject(reason);
    }

    let receiver_signing_key = match receiver_identity
        .get("pub_sig_b64")
        .and_then(JsonValue::as_str)
    {
        Some(value) if !value.is_empty() => value,
        _ => return reject("missing_receiver_signing_key"),
    };
    let receiver_fp = match id_fingerprint(receiver_signing_key) {
        Ok(fp) => fp,
        Err(reason) => return reject(reason),
    };

    directed_route(config, sender_fp, receiver_fp)
}

fn directed_route(
    config: &DedicatedRelayConfig,
    sender_fp: String,
    receiver_fp: String,
) -> RouteDecision {
    if config.policy.reject_unknown_receiver && !config.allowed_fingerprints.contains(&receiver_fp)
    {
        return reject("receiver_not_allowed");
    }

    if !graph_allows(config, &sender_fp, &receiver_fp) {
        return reject("route_not_allowed");
    }

    RouteDecision::Directed {
        sender_fp,
        receiver_fp,
    }
}

fn graph_receivers(config: &DedicatedRelayConfig, sender_fp: &str) -> Vec<String> {
    let mut receivers = match config.communication_graph.get(sender_fp) {
        Some(receivers) => receivers
            .iter()
            .filter(|receiver| {
                !config.policy.reject_unknown_receiver
                    || config.allowed_fingerprints.contains(receiver.as_str())
            })
            .cloned()
            .collect::<Vec<_>>(),
        None => Vec::new(),
    };
    receivers.sort();
    receivers
}

fn graph_allows(config: &DedicatedRelayConfig, sender_fp: &str, receiver_fp: &str) -> bool {
    config
        .communication_graph
        .get(sender_fp)
        .map(|receivers| receivers.contains(receiver_fp))
        .unwrap_or(false)
}

fn get_path<'a>(value: &'a JsonValue, path: &str) -> Option<&'a JsonValue> {
    let trimmed = path.trim();
    if trimmed.is_empty() {
        return Some(value);
    }

    let mut current = value;
    for segment in trimmed.split('.') {
        if segment.is_empty() {
            return None;
        }
        current = current.get(segment)?;
    }
    Some(current)
}

fn verify_public_id(public_id: &JsonValue) -> Result<(), &'static str> {
    let signing_key = public_id
        .get("pub_sig_b64")
        .and_then(JsonValue::as_str)
        .ok_or("missing_identity_signing_key")?;
    let signature = public_id
        .get("sig")
        .and_then(JsonValue::as_str)
        .ok_or("missing_identity_signature")?;
    verify_public_id_with_signature(public_id, signing_key, signature)
}

fn verify_public_id_with_signature(
    public_id: &JsonValue,
    signing_key: &str,
    identity_signature: &str,
) -> Result<(), &'static str> {
    if public_id.get("v").and_then(JsonValue::as_str) != Some("id.v1") {
        return Err("unsupported_identity_version");
    }

    let identity_key = public_id
        .get("pub_sig_b64")
        .and_then(JsonValue::as_str)
        .ok_or("missing_identity_signing_key")?;
    if identity_key != signing_key {
        return Err("identity_signing_key_mismatch");
    }

    let core = public_identity_core(public_id)?;
    verify_ed25519(
        signing_key,
        &canonical_json_bytes(&core)?,
        identity_signature,
    )
    .map_err(|_| "invalid_identity_signature")
}

fn public_identity_core(public_id: &JsonValue) -> Result<JsonValue, &'static str> {
    let mut out = Map::new();
    for key in ["created_at", "pub_enc_b64", "pub_sig_b64"] {
        let value = public_id.get(key).ok_or("invalid_identity_shape")?;
        out.insert(key.to_string(), value.clone());
    }
    if let Some(meta) = public_id.get("meta") {
        if !meta.is_null() {
            out.insert("meta".to_string(), meta.clone());
        }
    }
    Ok(JsonValue::Object(out))
}

fn verify_envelope_signature(
    root: &JsonValue,
    sender_identity: &JsonValue,
    receiver_identity: &JsonValue,
    sender_signing_key: &str,
    envelope_signature: &str,
) -> Result<(), &'static str> {
    if root.get("v").and_then(JsonValue::as_str) != Some("env.v1") {
        return Err("unsupported_envelope_version");
    }

    let core = json!({
        "v": root.get("v").ok_or("missing_envelope_version")?,
        "payload": root.get("payload").ok_or("missing_payload")?,
        "session_proof": root.get("session_proof").ok_or("missing_session_proof")?,
        "from": sender_identity,
        "to": receiver_identity,
    });

    verify_ed25519(
        sender_signing_key,
        &canonical_json_bytes(&core)?,
        envelope_signature,
    )
    .map_err(|_| "invalid_envelope_signature")
}

fn verify_ed25519(
    pub_key_b64: &str,
    message: &[u8],
    signature_b64: &str,
) -> Result<(), &'static str> {
    let key_bytes = STANDARD
        .decode(pub_key_b64)
        .map_err(|_| "invalid_public_key_base64")?;
    let key_bytes: [u8; 32] = key_bytes
        .try_into()
        .map_err(|_| "invalid_public_key_length")?;
    let verifying_key = VerifyingKey::from_bytes(&key_bytes).map_err(|_| "invalid_public_key")?;

    let signature_bytes = STANDARD
        .decode(signature_b64)
        .map_err(|_| "invalid_signature_base64")?;
    let signature_bytes: [u8; 64] = signature_bytes
        .try_into()
        .map_err(|_| "invalid_signature_length")?;
    let signature = Signature::from_bytes(&signature_bytes);

    verifying_key
        .verify(message, &signature)
        .map_err(|_| "signature_verify_failed")
}

fn id_fingerprint(pub_sig_b64: &str) -> Result<String, &'static str> {
    let raw = STANDARD
        .decode(pub_sig_b64)
        .map_err(|_| "invalid_fingerprint_key_base64")?;
    let digest = Sha256::digest(raw);
    Ok(URL_SAFE_NO_PAD.encode(digest)[..22].to_string())
}

fn canonical_json_bytes(value: &JsonValue) -> Result<Vec<u8>, &'static str> {
    serde_json::to_string(&sort_json_value(value))
        .map(|compact| escape_json_non_ascii(&compact).into_bytes())
        .map_err(|_| "canonical_json_failed")
}

fn escape_json_non_ascii(input: &str) -> String {
    if input.is_ascii() {
        return input.to_string();
    }

    let mut out = String::with_capacity(input.len());
    for ch in input.chars() {
        if ch.is_ascii() {
            out.push(ch);
            continue;
        }

        let code = ch as u32;
        if code <= 0xffff {
            let _ = write!(&mut out, "\\u{code:04x}");
        } else {
            let scalar = code - 0x1_0000;
            let high = 0xd800 + ((scalar >> 10) & 0x3ff);
            let low = 0xdc00 + (scalar & 0x3ff);
            let _ = write!(&mut out, "\\u{high:04x}\\u{low:04x}");
        }
    }
    out
}

fn sort_json_value(value: &JsonValue) -> JsonValue {
    match value {
        JsonValue::Array(items) => JsonValue::Array(items.iter().map(sort_json_value).collect()),
        JsonValue::Object(map) => {
            let mut keys = map.keys().collect::<Vec<_>>();
            keys.sort();

            let mut sorted = Map::new();
            for key in keys {
                if let Some(value) = map.get(key) {
                    sorted.insert(key.clone(), sort_json_value(value));
                }
            }
            JsonValue::Object(sorted)
        }
        _ => value.clone(),
    }
}

fn reject(reason: impl Into<String>) -> RouteDecision {
    RouteDecision::Reject {
        reason: reason.into(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::{DedicatedRelayConfig, InvalidAttemptPolicy};
    use ed25519_dalek::{Signer, SigningKey};
    use std::net::IpAddr;

    fn signed_identity(seed: u8) -> (SigningKey, JsonValue, String) {
        let signing_key = SigningKey::from_bytes(&[seed; 32]);
        let verifying_key = signing_key.verifying_key();
        let pub_sig_b64 = STANDARD.encode(verifying_key.to_bytes());
        let pub_enc_b64 = STANDARD.encode([seed.saturating_add(1); 32]);

        let public_core = json!({
            "created_at": "2026-05-20T00:00:00+00:00",
            "pub_enc_b64": pub_enc_b64,
            "pub_sig_b64": pub_sig_b64,
        });
        let public_sig = signing_key.sign(&canonical_json_bytes(&public_core).unwrap());
        let public_id = json!({
            "created_at": public_core["created_at"],
            "pub_enc_b64": public_core["pub_enc_b64"],
            "pub_sig_b64": public_core["pub_sig_b64"],
            "sig": STANDARD.encode(public_sig.to_bytes()),
            "v": "id.v1",
        });
        let fingerprint = id_fingerprint(public_core["pub_sig_b64"].as_str().unwrap()).unwrap();

        (signing_key, public_id, fingerprint)
    }

    fn test_session() -> JsonValue {
        json!({
            "sender_role": 0,
            "0_nonce": "abc",
            "1_nonce": null,
            "ts": 1,
            "ttl": 30,
            "history_proof": null,
            "age": 0,
            "mode": "single",
            "stream": null,
            "stream_ttl": null,
        })
    }

    fn signed_envelope(
        signing_key: &SigningKey,
        public_id: &JsonValue,
        to: JsonValue,
    ) -> JsonValue {
        let envelope_core = json!({
            "v": "env.v1",
            "payload": {"kind": "collab_discovery"},
            "session_proof": test_session(),
            "from": public_id,
            "to": to,
        });
        let envelope_sig = signing_key.sign(&canonical_json_bytes(&envelope_core).unwrap());
        let mut envelope = envelope_core;
        envelope["sig"] = JsonValue::String(STANDARD.encode(envelope_sig.to_bytes()));
        envelope
    }

    fn enabled_config_with_sender(sender_fp: &str) -> DedicatedRelayConfig {
        let mut config = DedicatedRelayConfig::default();
        config.enabled = true;
        config.allowed_fingerprints.insert(sender_fp.to_string());
        config
    }

    #[test]
    fn path_lookup_supports_empty_root_and_dot_paths() {
        let value = json!({"important": {"from": {"pub_sig_b64": "abc"}}});
        assert_eq!(get_path(&value, "").unwrap(), &value);
        assert_eq!(
            get_path(&value, "important.from.pub_sig_b64").and_then(JsonValue::as_str),
            Some("abc")
        );
        assert!(get_path(&value, "important..from").is_none());
    }

    #[test]
    fn disabled_config_uses_legacy_broadcast() {
        let config = DedicatedRelayConfig::default();
        assert!(matches!(
            decide_route("not-json-is-fine-in-legacy", &config),
            RouteDecision::LegacyBroadcast
        ));
    }

    #[test]
    fn enabled_config_rejects_malformed_json() {
        let mut config = DedicatedRelayConfig::default();
        config.enabled = true;
        assert!(matches!(
            decide_route("not json", &config),
            RouteDecision::Reject { reason } if reason == "invalid_json"
        ));
    }

    #[test]
    fn canonical_json_uses_python_compatible_ascii_escaping() {
        let value = json!({
            "z": "é",
            "emoji": "😊",
            "nested": {
                "b": 2,
                "a": "é",
            },
        });

        let canonical = String::from_utf8(canonical_json_bytes(&value).unwrap()).unwrap();
        assert_eq!(
            canonical,
            r#"{"emoji":"\ud83d\ude0a","nested":{"a":"\u00e9","b":2},"z":"\u00e9"}"#
        );
    }

    #[test]
    fn valid_receiverless_envelope_routes_to_graph_neighbors() {
        let (signing_key, public_id, sender_fp) = signed_identity(7);
        let envelope = signed_envelope(&signing_key, &public_id, JsonValue::Null);
        let receiver_fp = "receiver-fp".to_string();
        let mut config = DedicatedRelayConfig::default();
        config.enabled = true;
        config.allowed_fingerprints.insert(sender_fp.clone());
        config.allowed_fingerprints.insert(receiver_fp.clone());
        config.communication_graph.insert(
            sender_fp.clone(),
            [receiver_fp.clone()].into_iter().collect(),
        );

        assert!(matches!(
            decide_route(&envelope.to_string(), &config),
            RouteDecision::GraphFanout {
                sender_fp: got_sender,
                receiver_fps,
            } if got_sender == sender_fp && receiver_fps == vec![receiver_fp]
        ));
    }

    #[test]
    fn enabled_config_rejects_missing_sender_identity() {
        let mut config = DedicatedRelayConfig::default();
        config.enabled = true;
        let content = json!({
            "v": "env.v1",
            "payload": {},
            "session_proof": {},
            "to": null,
            "sig": "not-used",
        });

        assert!(matches!(
            decide_route(&content.to_string(), &config),
            RouteDecision::Reject { reason } if reason == "missing_sender_identity"
        ));
    }

    #[test]
    fn sender_allowlist_rejection_happens_before_identity_signature_work() {
        let (signing_key, mut public_id, _sender_fp) = signed_identity(8);
        public_id["sig"] = JsonValue::String("not-a-valid-signature".to_string());
        let envelope = signed_envelope(&signing_key, &public_id, JsonValue::Null);
        let mut config = DedicatedRelayConfig::default();
        config.enabled = true;

        assert!(matches!(
            decide_route(&envelope.to_string(), &config),
            RouteDecision::Reject { reason } if reason == "sender_not_allowed"
        ));
    }

    #[test]
    fn invalid_sender_identity_signature_is_rejected_for_allowed_sender() {
        let (signing_key, mut public_id, sender_fp) = signed_identity(9);
        public_id["sig"] = JsonValue::String("not-a-valid-signature".to_string());
        let envelope = signed_envelope(&signing_key, &public_id, JsonValue::Null);
        let config = enabled_config_with_sender(&sender_fp);

        assert!(matches!(
            decide_route(&envelope.to_string(), &config),
            RouteDecision::Reject { reason } if reason == "invalid_identity_signature"
        ));
    }

    #[test]
    fn invalid_envelope_signature_is_rejected() {
        let (signing_key, public_id, sender_fp) = signed_identity(10);
        let mut envelope = signed_envelope(&signing_key, &public_id, JsonValue::Null);
        envelope["sig"] = JsonValue::String("not-a-valid-signature".to_string());
        let config = enabled_config_with_sender(&sender_fp);

        assert!(matches!(
            decide_route(&envelope.to_string(), &config),
            RouteDecision::Reject { reason } if reason == "invalid_envelope_signature"
        ));
    }

    #[test]
    fn receiver_fingerprint_string_routes_directed_message() {
        let (signing_key, public_id, sender_fp) = signed_identity(11);
        let receiver_fp = "receiver-fp".to_string();
        let envelope = signed_envelope(
            &signing_key,
            &public_id,
            JsonValue::String(receiver_fp.clone()),
        );
        let mut config = enabled_config_with_sender(&sender_fp);
        config.allowed_fingerprints.insert(receiver_fp.clone());
        config.communication_graph.insert(
            sender_fp.clone(),
            [receiver_fp.clone()].into_iter().collect(),
        );

        assert!(matches!(
            decide_route(&envelope.to_string(), &config),
            RouteDecision::Directed {
                sender_fp: got_sender,
                receiver_fp: got_receiver,
            } if got_sender == sender_fp && got_receiver == receiver_fp
        ));
    }

    #[test]
    fn valid_sender_rejects_receiver_not_in_allowlist() {
        let (signing_key, public_id, sender_fp) = signed_identity(12);
        let receiver_fp = "not-allowed-receiver".to_string();
        let envelope = signed_envelope(
            &signing_key,
            &public_id,
            JsonValue::String(receiver_fp.clone()),
        );
        let mut config = enabled_config_with_sender(&sender_fp);
        config
            .communication_graph
            .insert(sender_fp, [receiver_fp].into_iter().collect());

        assert!(matches!(
            decide_route(&envelope.to_string(), &config),
            RouteDecision::Reject { reason } if reason == "receiver_not_allowed"
        ));
    }

    #[test]
    fn valid_identities_reject_when_graph_lacks_edge() {
        let (signing_key, public_id, sender_fp) = signed_identity(13);
        let receiver_fp = "receiver-without-edge".to_string();
        let envelope = signed_envelope(
            &signing_key,
            &public_id,
            JsonValue::String(receiver_fp.clone()),
        );
        let mut config = enabled_config_with_sender(&sender_fp);
        config.allowed_fingerprints.insert(receiver_fp);

        assert!(matches!(
            decide_route(&envelope.to_string(), &config),
            RouteDecision::Reject { reason } if reason == "route_not_allowed"
        ));
    }

    #[test]
    fn invalid_attempt_tracker_escalates_by_ip() {
        let mut tracker = InvalidAttemptTracker::new();
        let policy = InvalidAttemptPolicy {
            enabled: true,
            window_secs: 60,
            max_entries: 100,
            throttle_threshold: 2,
            flow_control_threshold: 3,
            disconnect_threshold: 4,
            quarantine_threshold: 5,
        };
        let ip: IpAddr = "127.0.0.1".parse().unwrap();

        assert_eq!(
            tracker.record(ip, "invalid_json", &policy),
            InvalidAttemptAction::LogOnly
        );
        assert_eq!(
            tracker.record(ip, "invalid_json", &policy),
            InvalidAttemptAction::Throttle
        );
        assert_eq!(
            tracker.record(ip, "invalid_json", &policy),
            InvalidAttemptAction::FlowControl
        );
        assert_eq!(
            tracker.record(ip, "invalid_json", &policy),
            InvalidAttemptAction::Disconnect
        );
        assert_eq!(
            tracker.record(ip, "invalid_json", &policy),
            InvalidAttemptAction::Quarantine
        );
    }
}
