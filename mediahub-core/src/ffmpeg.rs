use std::path::{Path, PathBuf};
use std::process::Command;

use serde::{Deserialize, Serialize};

use crate::errors::MediaError;

/// Video metadata returned by ffprobe
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VideoMetadata {
    pub duration_ms: u64,
    pub width: u32,
    pub height: u32,
    pub codec: String,
    pub bitrate: Option<u64>,
    pub fps: Option<f32>,
}

/// Result of audio extraction
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AudioResult {
    pub output_path: String,
    pub duration_ms: u64,
    pub sample_rate: u32,
    pub channels: u32,
}

/// FFmpeg/FFprobe wrapper for media operations.
///
/// Locates ffmpeg and ffprobe on the system PATH (or at a configured path)
/// and provides high-level functions for audio extraction, metadata retrieval,
/// and thumbnail generation.
pub struct FfmpegWrapper {
    ffmpeg_path: PathBuf,
    ffprobe_path: PathBuf,
}

impl FfmpegWrapper {
    /// Create a new wrapper, auto-detecting ffmpeg/ffprobe from PATH or an
    /// optional override path set via the `FFMPEG_PATH` environment variable.
    pub fn new() -> Result<Self, MediaError> {
        let ffmpeg_path = Self::find_binary("ffmpeg", "FFMPEG_PATH")?;
        let ffprobe_path = Self::find_binary("ffprobe", "FFPROBE_PATH").unwrap_or_else(|_| {
            // Try to derive ffprobe path from ffmpeg path
            let mut probe = ffmpeg_path.clone();
            probe.set_file_name("ffprobe");
            probe
        });

        Ok(Self {
            ffmpeg_path,
            ffprobe_path,
        })
    }

    /// Create a wrapper using explicit paths.
    pub fn with_paths(ffmpeg: PathBuf, ffprobe: PathBuf) -> Self {
        Self {
            ffmpeg_path: ffmpeg,
            ffprobe_path: ffprobe,
        }
    }

    /// Find a binary by checking an env-var override first, then system PATH.
    fn find_binary(name: &str, env_key: &str) -> Result<PathBuf, MediaError> {
        // 1. Check environment variable override
        if let Ok(path) = std::env::var(env_key) {
            let p = PathBuf::from(&path);
            if p.exists() {
                return Ok(p);
            }
        }

        // 2. Check system PATH
        if let Ok(output) = Command::new(name).arg("-version").output() {
            if output.status.success() {
                return Ok(PathBuf::from(name));
            }
        }

        Err(if name == "ffmpeg" {
            MediaError::FfmpegNotAvailable
        } else {
            MediaError::FfprobeNotAvailable
        })
    }

    /// Check if ffmpeg is available.
    pub fn is_available(&self) -> bool {
        Command::new(&self.ffmpeg_path)
            .arg("-version")
            .output()
            .map(|o| o.status.success())
            .unwrap_or(false)
    }

    /// Extract audio from a video file to 16-bit PCM mono WAV.
    ///
    /// This is the format expected by Whisper for speech-to-text.
    ///
    /// # Arguments
    /// * `input`       - Path to the input video/audio file
    /// * `output`      - Path for the output WAV file
    /// * `sample_rate` - Sample rate in Hz (use 16000 for Whisper)
    pub fn extract_audio(
        &self,
        input: &Path,
        output: &Path,
        sample_rate: u32,
    ) -> Result<AudioResult, MediaError> {
        if !input.exists() {
            return Err(MediaError::FileNotFound(input.display().to_string()));
        }

        // Ensure output directory exists
        if let Some(parent) = output.parent() {
            std::fs::create_dir_all(parent)?;
        }

        let result = Command::new(&self.ffmpeg_path)
            .args([
                "-i",
                input.to_str().unwrap(),
                "-vn",          // disable video
                "-acodec",
                "pcm_s16le",    // 16-bit PCM
                "-ar",
                &sample_rate.to_string(),
                "-ac",
                "1",            // mono
                "-f",
                "wav",
                "-y",           // overwrite
                output.to_str().unwrap(),
            ])
            .output()
            .map_err(|e| MediaError::ExecutionFailed(e.to_string()))?;

        if !result.status.success() {
            let stderr = String::from_utf8_lossy(&result.stderr);
            return Err(MediaError::ExecutionFailed(format!(
                "Audio extraction failed: {}",
                stderr.lines().last().unwrap_or("unknown error")
            )));
        }

        // Get metadata of the extracted audio to return duration info
        let duration_ms = self
            .probe_duration(output)
            .unwrap_or(0);

        Ok(AudioResult {
            output_path: output.display().to_string(),
            duration_ms,
            sample_rate,
            channels: 1,
        })
    }

    /// Get video metadata via ffprobe (JSON output).
    pub fn get_video_metadata(&self, input: &Path) -> Result<VideoMetadata, MediaError> {
        if !input.exists() {
            return Err(MediaError::FileNotFound(input.display().to_string()));
        }

        let output = Command::new(&self.ffprobe_path)
            .args([
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,codec_name,bit_rate,r_frame_rate:format=duration",
                "-of", "json",
                input.to_str().unwrap(),
            ])
            .output()
            .map_err(|e| MediaError::ExecutionFailed(e.to_string()))?;

        if !output.status.success() {
            return Err(MediaError::ExecutionFailed(
                "Failed to get video metadata".to_string(),
            ));
        }

        let json_str = String::from_utf8_lossy(&output.stdout);
        Self::parse_video_metadata(&json_str)
    }

    /// Generate a thumbnail image from a video at a given timestamp.
    ///
    /// # Arguments
    /// * `input`        - Path to the input video
    /// * `output`       - Path for the output image (jpg/png)
    /// * `time_seconds` - Timestamp in seconds to capture
    pub fn generate_thumbnail(
        &self,
        input: &Path,
        output: &Path,
        time_seconds: f32,
    ) -> Result<(), MediaError> {
        if !input.exists() {
            return Err(MediaError::FileNotFound(input.display().to_string()));
        }

        if let Some(parent) = output.parent() {
            std::fs::create_dir_all(parent)?;
        }

        let result = Command::new(&self.ffmpeg_path)
            .args([
                "-ss",
                &time_seconds.to_string(),
                "-i",
                input.to_str().unwrap(),
                "-vframes",
                "1",
                "-y",
                output.to_str().unwrap(),
            ])
            .output()
            .map_err(|e| MediaError::ExecutionFailed(e.to_string()))?;

        if !result.status.success() {
            let stderr = String::from_utf8_lossy(&result.stderr);
            return Err(MediaError::ExecutionFailed(format!(
                "Thumbnail generation failed: {}",
                stderr.lines().last().unwrap_or("unknown error")
            )));
        }

        Ok(())
    }

    // ─── Internal helpers ───────────────────────────────────────────────

    /// Use ffprobe to get duration in milliseconds.
    fn probe_duration(&self, input: &Path) -> Result<u64, MediaError> {
        let output = Command::new(&self.ffprobe_path)
            .args([
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                input.to_str().unwrap(),
            ])
            .output()
            .map_err(|e| MediaError::ExecutionFailed(e.to_string()))?;

        if !output.status.success() {
            return Err(MediaError::ParseFailed("ffprobe duration query failed".to_string()));
        }

        let s = String::from_utf8_lossy(&output.stdout);
        let secs: f64 = s
            .trim()
            .parse()
            .map_err(|_| MediaError::ParseFailed(format!("Cannot parse duration: {}", s.trim())))?;

        Ok((secs * 1000.0) as u64)
    }

    /// Parse the JSON output of ffprobe into a VideoMetadata struct.
    fn parse_video_metadata(json_str: &str) -> Result<VideoMetadata, MediaError> {
        let json: serde_json::Value =
            serde_json::from_str(json_str).map_err(|e| MediaError::ParseFailed(e.to_string()))?;

        let streams = json.get("streams").and_then(|s| s.as_array());
        let format = json.get("format");

        let (width, height, codec, bitrate, fps) = if let Some(stream) =
            streams.and_then(|s| s.first())
        {
            let w = stream.get("width").and_then(|v| v.as_u64()).unwrap_or(0) as u32;
            let h = stream.get("height").and_then(|v| v.as_u64()).unwrap_or(0) as u32;
            let c = stream
                .get("codec_name")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown")
                .to_string();
            let br = stream
                .get("bit_rate")
                .and_then(|v| v.as_str())
                .and_then(|s| s.parse().ok());
            let f = stream
                .get("r_frame_rate")
                .and_then(|v| v.as_str())
                .and_then(|s| Self::parse_frame_rate(s));
            (w, h, c, br, f)
        } else {
            (0, 0, "unknown".to_string(), None, None)
        };

        let duration_ms = format
            .and_then(|f| f.get("duration"))
            .and_then(|d| d.as_str())
            .and_then(|s| s.parse::<f64>().ok())
            .map(|d| (d * 1000.0) as u64)
            .unwrap_or(0);

        Ok(VideoMetadata {
            duration_ms,
            width,
            height,
            codec,
            bitrate,
            fps,
        })
    }

    /// Parse frame-rate strings like "30/1" or "29.97".
    fn parse_frame_rate(fps_str: &str) -> Option<f32> {
        if fps_str.contains('/') {
            let parts: Vec<&str> = fps_str.split('/').collect();
            if parts.len() == 2 {
                let num: f32 = parts[0].parse().ok()?;
                let den: f32 = parts[1].parse().ok()?;
                if den > 0.0 {
                    return Some(num / den);
                }
            }
        }
        fps_str.parse().ok()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_frame_rate() {
        assert_eq!(FfmpegWrapper::parse_frame_rate("30/1"), Some(30.0));
        assert_eq!(
            FfmpegWrapper::parse_frame_rate("60000/1001"),
            Some(59.94006)
        );
        assert_eq!(FfmpegWrapper::parse_frame_rate("29.97"), Some(29.97));
        assert_eq!(FfmpegWrapper::parse_frame_rate("0/0"), None);
    }

    #[test]
    fn test_parse_video_metadata_valid() {
        let json = r#"{
            "streams": [{
                "width": 1920,
                "height": 1080,
                "codec_name": "h264",
                "bit_rate": "5000000",
                "r_frame_rate": "30/1"
            }],
            "format": {
                "duration": "120.500"
            }
        }"#;

        let meta = FfmpegWrapper::parse_video_metadata(json).unwrap();
        assert_eq!(meta.width, 1920);
        assert_eq!(meta.height, 1080);
        assert_eq!(meta.codec, "h264");
        assert_eq!(meta.bitrate, Some(5000000));
        assert_eq!(meta.fps, Some(30.0));
        assert_eq!(meta.duration_ms, 120500);
    }

    #[test]
    fn test_parse_video_metadata_empty_streams() {
        let json = r#"{"streams": [], "format": {"duration": "10.0"}}"#;
        let meta = FfmpegWrapper::parse_video_metadata(json).unwrap();
        assert_eq!(meta.width, 0);
        assert_eq!(meta.height, 0);
        assert_eq!(meta.codec, "unknown");
        assert_eq!(meta.duration_ms, 10000);
    }
}
