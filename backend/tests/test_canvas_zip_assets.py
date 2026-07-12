"""Canvas asset zip helpers (P2-7): whitelist URL parsing + name dedup."""

from app.services.canvas.zip_assets import (
    MAX_ZIP_ITEMS,
    dedupe_zip_name,
    parse_generated_media_id,
)


class TestParseGeneratedMediaId:
    def test_accepts_the_three_serve_urls(self):
        assert parse_generated_media_id("/api/v1/generated-media/9/file") == 9
        assert parse_generated_media_id("/api/v1/generated-media/12/stream") == 12
        assert parse_generated_media_id("/api/v1/generated-media/3/cover") == 3

    def test_rejects_absolute_and_external_urls_no_ssrf(self):
        assert parse_generated_media_id("https://evil.example.com/x.png") is None
        assert parse_generated_media_id("http://169.254.169.254/latest/meta") is None
        assert (
            parse_generated_media_id(
                "https://app.example.com/api/v1/generated-media/9/file"
            )
            is None
        )

    def test_rejects_resource_files_and_other_paths(self):
        assert parse_generated_media_id("/api/v1/resources/9/file") is None
        assert parse_generated_media_id("/api/v1/generated-media/9/promote") is None
        assert parse_generated_media_id("/api/v1/generated-media/9") is None

    def test_rejects_query_strings_and_traversal(self):
        assert parse_generated_media_id("/api/v1/generated-media/9/file?x=1") is None
        assert parse_generated_media_id("/api/v1/generated-media/../9/file") is None

    def test_non_string_input(self):
        assert parse_generated_media_id(None) is None  # type: ignore[arg-type]
        assert parse_generated_media_id(123) is None  # type: ignore[arg-type]


class TestDedupeZipName:
    def test_unique_names_pass_through(self):
        taken: set[str] = set()
        assert dedupe_zip_name("a.png", taken) == "a.png"
        assert dedupe_zip_name("b.png", taken) == "b.png"

    def test_collisions_get_numeric_suffix_before_the_extension(self):
        taken: set[str] = set()
        assert dedupe_zip_name("cat.png", taken) == "cat.png"
        assert dedupe_zip_name("cat.png", taken) == "cat-2.png"
        assert dedupe_zip_name("cat.png", taken) == "cat-3.png"

    def test_extensionless_names(self):
        taken: set[str] = set()
        assert dedupe_zip_name("clip", taken) == "clip"
        assert dedupe_zip_name("clip", taken) == "clip-2"

    def test_blank_name_falls_back(self):
        taken: set[str] = set()
        assert dedupe_zip_name("", taken) == "file"
        assert dedupe_zip_name("   ", taken) == "file-2"

    def test_max_items_constant_is_sane(self):
        assert 1 < MAX_ZIP_ITEMS <= 256
