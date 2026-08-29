//! 流式 HTTP 拉取 —— 把字节搬运从 Python 解释器里挪出来。
//!
//! Python 侧只负责算出签名 URL 与 headers（鉴权决策），字节完全不经过
//! 解释器。实测 Python chunk 循环把 470 MB/s 压到 243 MB/s。

use std::path::Path;

use futures_util::StreamExt;
use tokio::io::AsyncWriteExt;
use tokio_util::io::ReaderStream;

use crate::errors::MediaError;

/// 拉取 `url` 并流式写入 `dst`，返回写入字节数。
///
/// 失败时**必须**删除半成品文件：调用方 `materialize()` 会把 dst 交给
/// ffmpeg，一个被截断的文件不会报错，只会产出静默错误的结果 —— 那是
/// 最难排查的故障形态。
pub async fn fetch_to_file(
    url: &str,
    headers: Vec<(String, String)>,
    dst: &str,
) -> Result<u64, MediaError> {
    match fetch_inner(url, headers, dst).await {
        Ok(n) => Ok(n),
        Err(e) => {
            let _ = tokio::fs::remove_file(Path::new(dst)).await;
            Err(e)
        }
    }
}

async fn fetch_inner(
    url: &str,
    headers: Vec<(String, String)>,
    dst: &str,
) -> Result<u64, MediaError> {
    let client = reqwest::Client::new();
    let mut req = client.get(url);
    for (k, v) in headers {
        req = req.header(k, v);
    }

    let resp = req.send().await.map_err(|e| MediaError::Http(e.to_string()))?;
    let status = resp.status();
    if !status.is_success() {
        return Err(MediaError::HttpStatus(status.as_u16(), url.to_string()));
    }

    let mut file = tokio::fs::File::create(dst)
        .await
        .map_err(|e| MediaError::Io(e.to_string()))?;

    let mut written: u64 = 0;
    let mut stream = resp.bytes_stream();
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|e| MediaError::Http(e.to_string()))?;
        file.write_all(&chunk)
            .await
            .map_err(|e| MediaError::Io(e.to_string()))?;
        written += chunk.len() as u64;
    }
    file.flush().await.map_err(|e| MediaError::Io(e.to_string()))?;
    Ok(written)
}

/// 把本地文件 `src` 流式 PUT 到 `url`。
///
/// 用 `Body::wrap_stream` 而非读进内存 —— 生成视频可达数百 MB，一次性
/// `read()` 整个文件会在 Python 进程里凭空多出一份文件大小的内存占用。
pub async fn put_file(
    src: &str,
    url: &str,
    headers: Vec<(String, String)>,
) -> Result<(), MediaError> {
    let file = tokio::fs::File::open(src)
        .await
        .map_err(|e| MediaError::Io(e.to_string()))?;
    let len = file
        .metadata()
        .await
        .map_err(|e| MediaError::Io(e.to_string()))?
        .len();

    let stream = ReaderStream::new(file);
    let client = reqwest::Client::new();
    let mut req = client.put(url).header("Content-Length", len);
    for (k, v) in headers {
        req = req.header(k, v);
    }

    let resp = req
        .body(reqwest::Body::wrap_stream(stream))
        .send()
        .await
        .map_err(|e| MediaError::Http(e.to_string()))?;

    let status = resp.status();
    if !status.is_success() {
        return Err(MediaError::HttpStatus(status.as_u16(), url.to_string()));
    }
    Ok(())
}
