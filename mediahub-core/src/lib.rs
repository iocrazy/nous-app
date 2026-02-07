// PyO3 macro-generated code triggers useless_conversion for PyResult error types
#![allow(clippy::useless_conversion)]

pub mod errors;
pub mod ffmpeg;
pub mod hls;

use std::path::Path;

use pyo3::prelude::*;
use pyo3::types::PyDict;

use ffmpeg::FfmpegWrapper;
use hls::HlsSegmenter;

// ─── Python-exposed functions ───────────────────────────────────────────────

/// Extract audio from a video file to 16-bit PCM mono WAV.
///
/// Args:
///     input_path:   Path to the input video file.
///     output_path:  Path for the output WAV file.
///     sample_rate:  Sample rate in Hz (use 16000 for Whisper).
///
/// Returns:
///     dict with keys: output_path, duration_ms, sample_rate, channels
#[pyfunction]
#[pyo3(signature = (input_path, output_path, sample_rate = 16000))]
fn extract_audio<'py>(
    py: Python<'py>,
    input_path: &str,
    output_path: &str,
    sample_rate: u32,
) -> PyResult<Bound<'py, PyDict>> {
    let wrapper = FfmpegWrapper::new()?;
    let result = wrapper.extract_audio(
        Path::new(input_path),
        Path::new(output_path),
        sample_rate,
    )?;

    let dict = PyDict::new_bound(py);
    dict.set_item("output_path", result.output_path)?;
    dict.set_item("duration_ms", result.duration_ms)?;
    dict.set_item("sample_rate", result.sample_rate)?;
    dict.set_item("channels", result.channels)?;
    Ok(dict)
}

/// Get video metadata (duration, resolution, codec, bitrate, fps).
///
/// Args:
///     input_path: Path to the video file.
///
/// Returns:
///     dict with keys: duration_ms, width, height, codec, bitrate, fps
#[pyfunction]
fn get_video_metadata<'py>(py: Python<'py>, input_path: &str) -> PyResult<Bound<'py, PyDict>> {
    let wrapper = FfmpegWrapper::new()?;
    let meta = wrapper.get_video_metadata(Path::new(input_path))?;

    let dict = PyDict::new_bound(py);
    dict.set_item("duration_ms", meta.duration_ms)?;
    dict.set_item("width", meta.width)?;
    dict.set_item("height", meta.height)?;
    dict.set_item("codec", meta.codec)?;
    dict.set_item("bitrate", meta.bitrate)?;
    dict.set_item("fps", meta.fps)?;
    Ok(dict)
}

/// Generate a thumbnail image from a video at a given timestamp.
///
/// Args:
///     input_path:   Path to the input video file.
///     output_path:  Path for the output image (jpg/png).
///     time_seconds: Timestamp in seconds to capture (default: 1.0).
#[pyfunction]
#[pyo3(signature = (input_path, output_path, time_seconds = 1.0))]
fn generate_thumbnail(input_path: &str, output_path: &str, time_seconds: f32) -> PyResult<()> {
    let wrapper = FfmpegWrapper::new()?;
    wrapper.generate_thumbnail(
        Path::new(input_path),
        Path::new(output_path),
        time_seconds,
    )?;
    Ok(())
}

/// Segment a video into HLS fMP4 segments (no re-encoding).
///
/// Args:
///     input_path:       Path to the input MP4 file.
///     output_dir:       Directory for the HLS output.
///     segment_duration: Target segment length in seconds (default: 6).
///     delete_original:  Delete the source MP4 after segmentation (default: False).
///
/// Returns:
///     dict with keys: playlist_path, segment_count, duration_ms
#[pyfunction]
#[pyo3(signature = (input_path, output_dir, segment_duration = 6, delete_original = false))]
fn segment_video<'py>(
    py: Python<'py>,
    input_path: &str,
    output_dir: &str,
    segment_duration: u32,
    delete_original: bool,
) -> PyResult<Bound<'py, PyDict>> {
    let ffmpeg_path = std::env::var("FFMPEG_PATH").unwrap_or_else(|_| "ffmpeg".to_string());
    let segmenter = HlsSegmenter::new(&ffmpeg_path);

    let result = segmenter.segment_video(
        Path::new(input_path),
        Path::new(output_dir),
        segment_duration,
        delete_original,
    )?;

    let dict = PyDict::new_bound(py);
    dict.set_item("playlist_path", result.playlist_path)?;
    dict.set_item("segment_count", result.segment_count)?;
    dict.set_item("duration_ms", result.duration_ms)?;
    Ok(dict)
}

/// Check whether a directory already contains HLS segments.
///
/// Args:
///     path: Directory path to check.
///
/// Returns:
///     True if a playlist.m3u8 exists in the directory.
#[pyfunction]
fn is_segmented(path: &str) -> bool {
    HlsSegmenter::is_segmented(Path::new(path))
}

/// Clean up HLS segment files from a directory.
///
/// Removes playlist.m3u8, init.mp4, and all .m4s files.
///
/// Args:
///     dir_path: Directory containing HLS segments.
#[pyfunction]
fn cleanup_segments(dir_path: &str) -> PyResult<()> {
    HlsSegmenter::cleanup_segments(Path::new(dir_path))?;
    Ok(())
}

/// MediaHub core module — high-performance media processing via Rust + FFmpeg.
#[pymodule]
fn mediahub_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(extract_audio, m)?)?;
    m.add_function(wrap_pyfunction!(get_video_metadata, m)?)?;
    m.add_function(wrap_pyfunction!(generate_thumbnail, m)?)?;
    m.add_function(wrap_pyfunction!(segment_video, m)?)?;
    m.add_function(wrap_pyfunction!(is_segmented, m)?)?;
    m.add_function(wrap_pyfunction!(cleanup_segments, m)?)?;
    Ok(())
}
