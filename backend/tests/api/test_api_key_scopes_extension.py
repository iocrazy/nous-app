# backend/tests/api/test_api_key_scopes_extension.py

"""API-key scope mappings used by the browser extension's Scan Images import.

The API-key auth path (`_validate_api_key`) denies any endpoint missing from
``ENDPOINT_SCOPE_MAP`` (least privilege), so each endpoint the extension calls
must be explicitly mapped. These tests pin the mappings the scan-import flow
depends on: scope picker (teams), folder picker, multipart upload, and the
optional post-import batch auto-tag.
"""

import pytest

from app.core.api_key_scopes import (
    ApiKeyScope,
    check_scope_permission,
    get_required_scopes,
    validate_scopes,
)


class TestScanImportEndpointScopes:
    def test_upload_requires_resources_write(self):
        scopes = get_required_scopes("POST", "/resources/upload")
        assert ApiKeyScope.RESOURCES_WRITE.value in scopes
        assert ApiKeyScope.RESOURCES_ALL.value in scopes

    def test_folders_list_requires_resources_read(self):
        scopes = get_required_scopes("GET", "/resources/folders/list")
        assert ApiKeyScope.RESOURCES_READ.value in scopes
        assert ApiKeyScope.RESOURCES_ALL.value in scopes

    def test_ai_batch_requires_resources_write(self):
        scopes = get_required_scopes("POST", "/resources/ai/batch")
        assert ApiKeyScope.RESOURCES_WRITE.value in scopes
        assert ApiKeyScope.RESOURCES_ALL.value in scopes

    def test_teams_list_requires_teams_read(self):
        scopes = get_required_scopes("GET", "/teams")
        assert ApiKeyScope.TEAMS_READ.value in scopes

    def test_unmapped_endpoint_still_denied(self):
        # Deny-by-default must stay intact for endpoints we did NOT map.
        assert get_required_scopes("DELETE", "/resources/upload") == []
        assert get_required_scopes("POST", "/teams") == []


class TestTeamsReadScope:
    def test_teams_read_is_a_valid_scope(self):
        ok, invalid = validate_scopes([ApiKeyScope.TEAMS_READ.value])
        assert ok, f"teams:read rejected: {invalid}"

    def test_teams_read_grants_teams_list(self):
        required = get_required_scopes("GET", "/teams")
        assert check_scope_permission(required, [ApiKeyScope.TEAMS_READ.value])

    def test_resources_scope_does_not_grant_teams_list(self):
        required = get_required_scopes("GET", "/teams")
        assert not check_scope_permission(
            required, [ApiKeyScope.RESOURCES_ALL.value]
        )

    @pytest.mark.parametrize(
        "user_scopes,expected",
        [
            ([ApiKeyScope.RESOURCES_WRITE.value], True),
            ([ApiKeyScope.RESOURCES_ALL.value], True),
            ([ApiKeyScope.RESOURCES_READ.value], False),
            ([ApiKeyScope.TAGS_ALL.value], False),
            (["*"], True),
        ],
    )
    def test_upload_permission_matrix(self, user_scopes, expected):
        required = get_required_scopes("POST", "/resources/upload")
        assert check_scope_permission(required, user_scopes) is expected
