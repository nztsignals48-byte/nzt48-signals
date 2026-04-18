// HTTP /health endpoints. Scaffold exposes /health with 200 OK.

use anyhow::Result;
use tokio::io::{AsyncWriteExt, AsyncReadExt};
use tokio::net::TcpListener;

pub async fn serve(bind: &str) -> Result<()> {
    let listener = TcpListener::bind(bind).await?;
    tokio::spawn(async move {
        loop {
            if let Ok((mut s, _)) = listener.accept().await {
                let _ = s.read(&mut [0u8; 256]).await;
                let _ = s.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK").await;
            }
        }
    });
    Ok(())
}
