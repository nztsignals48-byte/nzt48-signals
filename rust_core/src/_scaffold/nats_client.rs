// NATS JetStream client. Phase 1 fills the JetStream publish/subscribe; scaffold stores URL.

use anyhow::Result;

pub struct NatsClient { pub url: String }

impl NatsClient {
    pub async fn connect(url: &str) -> Result<Self> {
        // Phase 1 replaces with real JetStream async-nats client.
        Ok(Self { url: url.to_string() })
    }

    pub async fn publish(&self, _subject: &str, _payload: &[u8]) -> Result<()> {
        // Phase 1 fill.
        Ok(())
    }
}
