//! AEGIS V5 IBKR bridge — publishes live ticks to NATS,
//! subscribes to dynamic universe rotations.
//!
//! Two NATS flows:
//!   tick stream (out):  ticks.live.{ticker}
//!   rotations  (in):    universe.rotation   → broker.apply_rotation
//!
//! The rotator's watchlist.v5.json is the source of truth for the top-100
//! live tickers (rotated every ~30s based on scanner hits). On each
//! rotation message we diff against active subs and add/remove up to 10
//! per cycle (anti-thrash). Held-position tickers are never evicted.

mod broker_router;
mod clock;
mod config;
mod exchange_profile;
mod ibkr_broker;
mod metrics_export;
mod types;

use std::collections::HashSet;
use std::sync::Arc;
use std::time::Duration;

use async_nats::ConnectOptions;
use futures::StreamExt;
use tokio::sync::mpsc;
use tokio::sync::Mutex;
use tokio::time::interval;
use tracing::{error, info, warn};
use tracing_subscriber::{fmt, EnvFilter};

use crate::ibkr_broker::{load_contracts, ContractSpec, IbkrBroker};

const SUBJECT_TICK_PREFIX: &str = "ticks.live.";
const SUBJECT_STATUS: &str = "ibkr.status";
const SUBJECT_ROTATION: &str = "universe.rotation";
const SUBJECT_ORDER_FILLED: &str = "orders.filled";
const SUBJECT_ORDER_REJECT: &str = "orders.reject";
const CONTRACTS_PATH: &str = "/Users/rr/aegis-v5/config/contracts.toml";

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    fmt()
        .with_env_filter(EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")))
        .init();

    info!("aegis-v5 IBKR bridge starting (paper-only)");

    let cfg = config::load_config()
        .map_err(|e| anyhow::anyhow!("config load failed: {e}"))?;
    info!(mode = ?cfg.mode, "config loaded");

    let nats_url =
        std::env::var("NATS_URL").unwrap_or_else(|_| "nats://127.0.0.1:4222".to_string());
    let nats = ConnectOptions::new()
        .name("aegis-v5-ibkr-bridge")
        .connect(&nats_url)
        .await
        .map_err(|e| anyhow::anyhow!("NATS connect ({nats_url}): {e}"))?;
    info!(url = %nats_url, "connected to NATS");

    // Seed universe from the static contracts file.
    let seed_contracts =
        load_contracts(CONTRACTS_PATH).map_err(|e| anyhow::anyhow!("contracts load: {e}"))?;
    info!(count = seed_contracts.len(), "seed contracts loaded");

    let (tick_tx, mut tick_rx) = mpsc::channel::<crate::types::MarketTick>(10_000);
    let broker = Arc::new(Mutex::new(IbkrBroker::new(&cfg, tick_tx.clone())));
    {
        let mut b = broker.lock().await;
        b.connect()
            .await
            .map_err(|e| anyhow::anyhow!("IBKR connect: {e}"))?;
        info!(
            host = %cfg.ibkr_host,
            port = cfg.ibkr_port,
            client_id = cfg.ibkr_client_id,
            "connected to IB Gateway"
        );
        let errors = b.subscribe_all(&seed_contracts).await;
        if !errors.is_empty() {
            warn!(err_count = errors.len(), "some seed subs failed (continuing)");
        }
        info!(active_subs = b.subscription_count(), "seed subscription phase complete");
    }

    // Track tickers with open paper positions so we never evict them.
    let held = Arc::new(Mutex::new(HashSet::<String>::new()));

    // --- Publisher task: broker ticks → NATS -------------------------------
    let publisher_nats = nats.clone();
    let publish_task = tokio::spawn(async move {
        let mut published: u64 = 0;
        let mut publish_errors: u64 = 0;
        while let Some(tick) = tick_rx.recv().await {
            let subject = format!("{SUBJECT_TICK_PREFIX}{}", tick.ticker);
            let payload = match serde_json::to_vec(&tick) {
                Ok(b) => b,
                Err(e) => {
                    warn!(error = %e, "tick serialise failed — dropping");
                    publish_errors += 1;
                    continue;
                }
            };
            match publisher_nats.publish(subject, payload.into()).await {
                Ok(()) => {
                    published += 1;
                    if published % 1000 == 0 {
                        info!(published, errors = publish_errors, "NATS publish heartbeat");
                    }
                }
                Err(e) => {
                    publish_errors += 1;
                    error!(error = %e, "NATS publish failed");
                }
            }
        }
    });

    // --- Rotation consumer: NATS universe.rotation → broker.apply_rotation -
    let rotation_nats = nats.clone();
    let rotation_broker = broker.clone();
    let rotation_held = held.clone();
    let rotation_task = tokio::spawn(async move {
        let mut sub = match rotation_nats.subscribe(SUBJECT_ROTATION).await {
            Ok(s) => s,
            Err(e) => {
                error!(error = %e, "failed to subscribe {}", SUBJECT_ROTATION);
                return;
            }
        };
        info!(subject = SUBJECT_ROTATION, "listening for universe rotations");
        while let Some(msg) = sub.next().await {
            let payload: serde_json::Value = match serde_json::from_slice(&msg.payload) {
                Ok(v) => v,
                Err(e) => {
                    warn!(error = %e, "bad rotation payload");
                    continue;
                }
            };
            // Rotator publishes {ts, added, evicted, size}. We re-read the
            // watchlist file for the authoritative desired list.
            match tokio::fs::read_to_string("/Users/rr/aegis-v5/data/watchlist.v5.json").await {
                Ok(txt) => {
                    let list: Vec<serde_json::Value> = match serde_json::from_str(&txt) {
                        Ok(v) => v,
                        Err(e) => {
                            warn!(error = %e, "watchlist parse failed");
                            continue;
                        }
                    };
                    let desired: Vec<ContractSpec> = list
                        .into_iter()
                        .filter_map(|entry| {
                            let symbol = entry.get("ticker")?.as_str()?.to_string();
                            let exchange = entry.get("exchange").and_then(|v| v.as_str()).unwrap_or("SMART").to_string();
                            let con_id = entry.get("con_id").and_then(|v| v.as_i64()).unwrap_or(0);
                            if con_id == 0 {
                                return None;
                            }
                            Some(ContractSpec {
                                symbol,
                                exchange,
                                currency: entry.get("currency").and_then(|v| v.as_str()).unwrap_or("USD").to_string(),
                                con_id,
                                sec_type: "STK".to_string(),
                                fast_path: entry.get("fast").and_then(|v| v.as_bool()).unwrap_or(false),
                            })
                        })
                        .collect();

                    let held_set = rotation_held.lock().await.clone();
                    let mut b = rotation_broker.lock().await;
                    let (added, removed) = b.apply_rotation(&desired, &held_set).await;
                    if !added.is_empty() || !removed.is_empty() {
                        info!(
                            added = added.len(),
                            removed = removed.len(),
                            live = b.subscription_count(),
                            note = %payload.get("ts").map(|v| v.to_string()).unwrap_or_default(),
                            "rotation applied"
                        );
                    }
                }
                Err(e) => warn!(error = %e, "watchlist read failed"),
            }
        }
    });

    // --- Held-tickers consumer: track open positions -----------------------
    let held_nats = nats.clone();
    let held_for_task = held.clone();
    let held_task = tokio::spawn(async move {
        let mut filled_sub = match held_nats.subscribe(SUBJECT_ORDER_FILLED).await {
            Ok(s) => s,
            Err(e) => {
                error!(error = %e, "failed to subscribe orders.filled");
                return;
            }
        };
        while let Some(msg) = filled_sub.next().await {
            if let Ok(v) = serde_json::from_slice::<serde_json::Value>(&msg.payload) {
                if let Some(t) = v.get("ticker").and_then(|s| s.as_str()) {
                    let mut h = held_for_task.lock().await;
                    h.insert(t.to_string());
                }
            }
        }
    });

    // --- Status heartbeat --------------------------------------------------
    let status_nats = nats.clone();
    let status_broker = broker.clone();
    tokio::spawn(async move {
        let mut tick = interval(Duration::from_secs(5));
        loop {
            tick.tick().await;
            let subs = status_broker.lock().await.subscription_count();
            let status = serde_json::json!({
                "ts": chrono::Utc::now().to_rfc3339(),
                "connected": true,
                "subscriptions": subs,
                "nats": "ok",
            });
            if let Ok(bytes) = serde_json::to_vec(&status) {
                let _ = status_nats.publish(SUBJECT_STATUS, bytes.into()).await;
            }
        }
    });

    // --- Main poll loop (100 ms) -------------------------------------------
    let shutdown = async {
        tokio::signal::ctrl_c().await.ok();
        info!("SIGINT received — shutting down");
    };
    tokio::pin!(shutdown);

    let mut poll = interval(Duration::from_millis(100));
    loop {
        tokio::select! {
            _ = poll.tick() => {
                let mut b = broker.lock().await;
                b.poll_ticks().await;
                if !b.is_connected() {
                    warn!("IBKR disconnected — exiting poll loop");
                    break;
                }
            }
            _ = &mut shutdown => break,
        }
    }

    publish_task.abort();
    rotation_task.abort();
    held_task.abort();
    let _ = nats.flush().await;
    info!("aegis-v5 IBKR bridge stopped");
    // Silence unused-import noise for subjects consumed by future phases.
    let _ = SUBJECT_ORDER_REJECT;
    Ok(())
}
