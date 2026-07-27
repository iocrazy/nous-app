use pyo3::exceptions::PyRuntimeError;
use pyo3::PyErr;
use thiserror::Error;

#[derive(Error, Debug)]
pub enum MediaError {
    #[error("FFmpeg not available: ensure ffmpeg is installed and in PATH")]
    FfmpegNotAvailable,

    #[error("FFprobe not available: ensure ffprobe is installed and in PATH")]
    FfprobeNotAvailable,

    #[error("Command execution failed: {0}")]
    ExecutionFailed(String),

    #[error("Output parse failed: {0}")]
    ParseFailed(String),

    #[error("File not found: {0}")]
    FileNotFound(String),

    #[error("Unsupported format: {0}")]
    UnsupportedFormat(String),

    #[error("IO error: {0}")]
    IoError(String),

    #[error("HLS segmentation failed: {0}")]
    HlsError(String),
}

impl From<std::io::Error> for MediaError {
    fn from(err: std::io::Error) -> Self {
        MediaError::IoError(err.to_string())
    }
}

impl From<MediaError> for PyErr {
    fn from(err: MediaError) -> Self {
        PyRuntimeError::new_err(err.to_string())
    }
}
