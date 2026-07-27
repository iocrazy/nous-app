use std::path::Path;
use std::process::Command;

use serde::{Deserialize, Serialize};

use crate::errors::MediaError;

/// Result of HLS segmentation
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HlsResult {
    /// Path to the generated .m3u8 playlist
    pub playlist_path: String,
    /// Number of segments created
    pub segment_count: u32,
    /// Total duration in milliseconds
    pub duration_ms: u64,
}

/// HLS segmentation using ffmpeg -c copy (no re-encoding).
///
/// Converts an MP4 file into fMP4 HLS segments, suitable for adaptive
/// streaming via nginx or any HLS-compatible server.
pub struct HlsSegmenter {
    ffmpeg_path: String,
}

impl HlsSegmenter {
    /// Create a segmenter with an explicit ffmpeg path.
    pub fn new(ffmpeg_path: &str) -> Self {
        Self {
            ffmpeg_path: ffmpeg_path.to_string(),
        }
    }

    /// Segment a video into HLS fMP4 segments.
    ///
    /// Uses `ffmpeg -c copy` so there is **no re-encoding** — this is very
    /// fast even for large files.
    ///
    /// # Arguments
    /// * `input`            - Path to the input MP4 file
    /// * `output_dir`       - Directory where segments and playlist will be written
    /// * `segment_duration` - Target segment duration in seconds (default: 6)
    /// * `delete_original`  - If true, delete the original MP4 after successful segmentation
    ///
    /// # Output structure
    /// ```text
    /// output_dir/
    ///   playlist.m3u8
    ///   init.mp4
    ///   segment_000.m4s
    ///   segment_001.m4s
    ///   ...
    /// ```
    pub fn segment_video(
        &self,
        input: &Path,
        output_dir: &Path,
        segment_duration: u32,
        delete_original: bool,
    ) -> Result<HlsResult, MediaError> {
        if !input.exists() {
            return Err(MediaError::FileNotFound(input.display().to_string()));
        }

        // Ensure output directory exists
        std::fs::create_dir_all(output_dir)?;

        let playlist_path = output_dir.join("playlist.m3u8");

        let result = Command::new(&self.ffmpeg_path)
            .args([
                "-i",
                input.to_str().unwrap(),
                "-c",
                "copy",                     // no re-encoding
                "-hls_time",
                &segment_duration.to_string(),
                "-hls_segment_type",
                "fmp4",                      // fragmented MP4 segments
                "-hls_fmp4_init_filename",
                "init.mp4",
                "-hls_segment_filename",
                output_dir.join("segment_%03d.m4s").to_str().unwrap(),
                "-hls_list_size",
                "0",                         // keep all segments in playlist
                "-f",
                "hls",
                "-y",
                playlist_path.to_str().unwrap(),
            ])
            .output()
            .map_err(|e| MediaError::ExecutionFailed(e.to_string()))?;

        if !result.status.success() {
            let stderr = String::from_utf8_lossy(&result.stderr);
            return Err(MediaError::HlsError(format!(
                "HLS segmentation failed: {}",
                stderr.lines().last().unwrap_or("unknown error")
            )));
        }

        // Count segments
        let segment_count = Self::count_segments(output_dir)?;

        // Get duration from playlist
        let duration_ms = Self::parse_playlist_duration(&playlist_path).unwrap_or(0);

        // Optionally delete original
        if delete_original {
            if let Err(e) = std::fs::remove_file(input) {
                // Non-fatal: log but don't fail the operation
                eprintln!(
                    "Warning: could not delete original file {}: {}",
                    input.display(),
                    e
                );
            }
        }

        Ok(HlsResult {
            playlist_path: playlist_path.display().to_string(),
            segment_count,
            duration_ms,
        })
    }

    /// Check whether a directory already contains HLS segments.
    ///
    /// Returns true if a `playlist.m3u8` file exists in the directory.
    pub fn is_segmented(path: &Path) -> bool {
        path.join("playlist.m3u8").exists()
    }

    /// Remove all HLS segment files from a directory.
    ///
    /// Deletes `playlist.m3u8`, `init.mp4`, and any `.m4s` segment files.
    pub fn cleanup_segments(dir: &Path) -> Result<(), MediaError> {
        if !dir.exists() {
            return Ok(());
        }

        let entries =
            std::fs::read_dir(dir).map_err(|e| MediaError::IoError(e.to_string()))?;

        for entry in entries {
            let entry = entry.map_err(|e| MediaError::IoError(e.to_string()))?;
            let path = entry.path();

            if path.is_file() {
                let should_delete = path
                    .extension()
                    .and_then(|ext| ext.to_str())
                    .map(|ext| matches!(ext, "m3u8" | "m4s" | "mp4"))
                    .unwrap_or(false);

                if should_delete {
                    std::fs::remove_file(&path)?;
                }
            }
        }

        // Try to remove the directory itself if it is now empty
        let _ = std::fs::remove_dir(dir);

        Ok(())
    }

    // ─── Internal helpers ───────────────────────────────────────────────

    /// Count .m4s segment files in a directory.
    fn count_segments(dir: &Path) -> Result<u32, MediaError> {
        let entries =
            std::fs::read_dir(dir).map_err(|e| MediaError::IoError(e.to_string()))?;

        let count = entries
            .filter_map(|e| e.ok())
            .filter(|e| {
                e.path()
                    .extension()
                    .and_then(|ext| ext.to_str())
                    .map(|ext| ext == "m4s")
                    .unwrap_or(false)
            })
            .count();

        Ok(count as u32)
    }

    /// Parse total duration from an HLS playlist by summing #EXTINF values.
    fn parse_playlist_duration(playlist: &Path) -> Result<u64, MediaError> {
        let content = std::fs::read_to_string(playlist)?;
        let mut total_secs: f64 = 0.0;

        for line in content.lines() {
            if let Some(stripped) = line.strip_prefix("#EXTINF:") {
                // Format: #EXTINF:6.000000,
                if let Some(duration_str) = stripped.split(',').next() {
                    if let Ok(d) = duration_str.trim().parse::<f64>() {
                        total_secs += d;
                    }
                }
            }
        }

        Ok((total_secs * 1000.0) as u64)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn test_is_segmented_false_for_empty_dir() {
        let dir = std::env::temp_dir().join("mediahub_test_hls_empty");
        let _ = std::fs::create_dir_all(&dir);
        assert!(!HlsSegmenter::is_segmented(&dir));
        let _ = std::fs::remove_dir(&dir);
    }

    #[test]
    fn test_is_segmented_true_when_playlist_exists() {
        let dir = std::env::temp_dir().join("mediahub_test_hls_exists");
        let _ = std::fs::create_dir_all(&dir);
        let playlist = dir.join("playlist.m3u8");
        std::fs::write(&playlist, "#EXTM3U\n").unwrap();

        assert!(HlsSegmenter::is_segmented(&dir));

        let _ = std::fs::remove_file(playlist);
        let _ = std::fs::remove_dir(&dir);
    }

    #[test]
    fn test_parse_playlist_duration() {
        let dir = std::env::temp_dir().join("mediahub_test_hls_duration");
        let _ = std::fs::create_dir_all(&dir);
        let playlist = dir.join("playlist.m3u8");

        let mut f = std::fs::File::create(&playlist).unwrap();
        writeln!(f, "#EXTM3U").unwrap();
        writeln!(f, "#EXT-X-VERSION:7").unwrap();
        writeln!(f, "#EXTINF:6.000000,").unwrap();
        writeln!(f, "segment_000.m4s").unwrap();
        writeln!(f, "#EXTINF:6.000000,").unwrap();
        writeln!(f, "segment_001.m4s").unwrap();
        writeln!(f, "#EXTINF:3.500000,").unwrap();
        writeln!(f, "segment_002.m4s").unwrap();
        writeln!(f, "#EXT-X-ENDLIST").unwrap();

        let duration = HlsSegmenter::parse_playlist_duration(&playlist).unwrap();
        assert_eq!(duration, 15500);

        let _ = std::fs::remove_file(playlist);
        let _ = std::fs::remove_dir(&dir);
    }

    #[test]
    fn test_cleanup_segments() {
        let dir = std::env::temp_dir().join("mediahub_test_hls_cleanup");
        let _ = std::fs::create_dir_all(&dir);

        // Create fake segment files
        std::fs::write(dir.join("playlist.m3u8"), "").unwrap();
        std::fs::write(dir.join("init.mp4"), "").unwrap();
        std::fs::write(dir.join("segment_000.m4s"), "").unwrap();
        std::fs::write(dir.join("segment_001.m4s"), "").unwrap();

        HlsSegmenter::cleanup_segments(&dir).unwrap();

        // Directory and files should be cleaned up
        assert!(!dir.join("playlist.m3u8").exists());
        assert!(!dir.join("init.mp4").exists());
        assert!(!dir.join("segment_000.m4s").exists());
    }
}
