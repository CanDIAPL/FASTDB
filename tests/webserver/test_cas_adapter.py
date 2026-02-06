"""Unit tests for CAS adapter.

These tests verify the pure CAS protocol adapter without Flask dependencies.
They test XML parsing, URL construction, and error handling.

Layer: Core (for pure functions) / Adapter (for CAS protocol handling)
Category: Specification Compliance, Boundary Condition, Failure Mode
"""

import pytest
from unittest.mock import Mock, patch

from webserver.cas_adapter import (
    CASAdapter,
    CASErrorType,
    CASValidationResult,
    compute_user_attributes,
    validate_username,
)


# =============================================================================
# [LAYER: Core] [CATEGORY: Boundary] Username validation tests
# =============================================================================

class TestValidateUsername:
    """Test username validation (pure function)."""

    def test_valid_alphanumeric(self):
        """Valid alphanumeric usernames pass."""
        assert validate_username("jsmith") is True
        assert validate_username("user123") is True
        assert validate_username("ABC123") is True

    def test_valid_with_allowed_chars(self):
        """Usernames with @, _, -, . are valid."""
        assert validate_username("john.smith") is True
        assert validate_username("john_smith") is True
        assert validate_username("john-smith") is True
        assert validate_username("john@slac.stanford.edu") is True
        assert validate_username("user.name_123-test@domain") is True

    def test_empty_username_invalid(self):
        """Empty username is invalid."""
        assert validate_username("") is False

    def test_invalid_special_chars(self):
        """Usernames with invalid characters fail."""
        assert validate_username("user name") is False  # space
        assert validate_username("user<script>") is False  # HTML
        assert validate_username("user;drop") is False  # semicolon
        assert validate_username("user'test") is False  # quote
        assert validate_username("user/test") is False  # slash


# =============================================================================
# [LAYER: Core] [CATEGORY: Specification] User attribute computation tests
# =============================================================================

class TestComputeUserAttributes:
    """Test pure function for computing user attributes from CAS response."""

    def test_extracts_mapped_attributes(self):
        """Extracts email and displayname using attribute map."""
        attrs = compute_user_attributes(
            cas_username="jsmith",
            cas_attributes={"mail": "jsmith@example.com", "cn": "John Smith"},
            attribute_map={"email": "mail", "displayname": "cn"},
        )
        assert attrs["username"] == "jsmith"
        assert attrs["email"] == "jsmith@example.com"
        assert attrs["displayname"] == "John Smith"

    def test_uses_defaults_when_missing(self):
        """Falls back to defaults when attributes are missing."""
        attrs = compute_user_attributes(
            cas_username="jsmith",
            cas_attributes={},
            attribute_map={"email": "mail", "displayname": "cn"},
        )
        assert attrs["username"] == "jsmith"
        assert attrs["email"] == "jsmith@slac.stanford.edu"
        assert attrs["displayname"] == "jsmith"

    def test_custom_default_email_domain(self):
        """Uses custom email domain when attribute missing."""
        attrs = compute_user_attributes(
            cas_username="jsmith",
            cas_attributes={},
            attribute_map={"email": "mail", "displayname": "cn"},
            default_email_domain="example.org",
        )
        assert attrs["email"] == "jsmith@example.org"

    def test_partial_attributes(self):
        """Handles case where only some attributes are present."""
        attrs = compute_user_attributes(
            cas_username="jsmith",
            cas_attributes={"mail": "john@example.com"},
            attribute_map={"email": "mail", "displayname": "cn"},
        )
        assert attrs["email"] == "john@example.com"
        assert attrs["displayname"] == "jsmith"  # falls back to username


# =============================================================================
# [LAYER: Adapter] [CATEGORY: Specification] URL construction tests
# =============================================================================

class TestCASAdapterURLs:
    """Test CAS URL construction."""

    def test_login_url_construction(self):
        """Login URL includes service parameter."""
        adapter = CASAdapter(
            cas_server_url="https://cas.example.com/cas",
            service_url="https://myapp.example.com/callback",
        )
        url = adapter.get_login_url()
        assert url.startswith("https://cas.example.com/cas/login?")
        assert "service=https%3A%2F%2Fmyapp.example.com%2Fcallback" in url

    def test_login_url_with_return_url(self):
        """Login URL can use custom return URL."""
        adapter = CASAdapter(
            cas_server_url="https://cas.example.com/cas",
            service_url="https://myapp.example.com/callback",
        )
        url = adapter.get_login_url(return_url="https://other.example.com/page")
        assert "service=https%3A%2F%2Fother.example.com%2Fpage" in url

    def test_logout_url_basic(self):
        """Logout URL without return URL."""
        adapter = CASAdapter(
            cas_server_url="https://cas.example.com/cas",
            service_url="https://myapp.example.com/callback",
        )
        url = adapter.get_logout_url()
        assert url == "https://cas.example.com/cas/logout"

    def test_logout_url_with_return(self):
        """Logout URL with return URL."""
        adapter = CASAdapter(
            cas_server_url="https://cas.example.com/cas",
            service_url="https://myapp.example.com/callback",
        )
        url = adapter.get_logout_url(return_url="https://myapp.example.com/")
        assert url.startswith("https://cas.example.com/cas/logout?")
        assert "service=https%3A%2F%2Fmyapp.example.com%2F" in url

    def test_trailing_slash_normalized(self):
        """Trailing slash on CAS URL is normalized."""
        adapter = CASAdapter(
            cas_server_url="https://cas.example.com/cas/",
            service_url="https://myapp.example.com/callback",
        )
        url = adapter.get_login_url()
        assert url.startswith("https://cas.example.com/cas/login")
        assert "example.com//cas" not in url


# =============================================================================
# [LAYER: Adapter] [CATEGORY: Specification] XML parsing tests
# =============================================================================

class TestCASXMLParsing:
    """Test CAS response XML parsing."""

    def test_successful_authentication_response(self):
        """Parse successful CAS 3.0 authentication response."""
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <cas:serviceResponse xmlns:cas="http://www.yale.edu/tp/cas">
            <cas:authenticationSuccess>
                <cas:user>jsmith</cas:user>
                <cas:attributes>
                    <cas:mail>jsmith@example.com</cas:mail>
                    <cas:cn>John Smith</cas:cn>
                </cas:attributes>
            </cas:authenticationSuccess>
        </cas:serviceResponse>
        """
        adapter = CASAdapter(
            cas_server_url="https://cas.example.com/cas",
            service_url="https://myapp.example.com/callback",
        )
        result = adapter._parse_cas_response(xml)

        assert result.success is True
        assert result.username == "jsmith"
        assert result.attributes["mail"] == "jsmith@example.com"
        assert result.attributes["cn"] == "John Smith"
        assert result.error_type == CASErrorType.NONE

    def test_authentication_failure_response(self):
        """Parse CAS authentication failure response."""
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <cas:serviceResponse xmlns:cas="http://www.yale.edu/tp/cas">
            <cas:authenticationFailure code="INVALID_TICKET">
                Ticket ST-123-abc not recognized
            </cas:authenticationFailure>
        </cas:serviceResponse>
        """
        adapter = CASAdapter(
            cas_server_url="https://cas.example.com/cas",
            service_url="https://myapp.example.com/callback",
        )
        result = adapter._parse_cas_response(xml)

        assert result.success is False
        assert result.error_type == CASErrorType.INVALID_TICKET
        assert "not recognized" in result.error_message

    def test_missing_username_in_success(self):
        """Handle malformed response with missing username."""
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <cas:serviceResponse xmlns:cas="http://www.yale.edu/tp/cas">
            <cas:authenticationSuccess>
                <cas:attributes>
                    <cas:mail>test@example.com</cas:mail>
                </cas:attributes>
            </cas:authenticationSuccess>
        </cas:serviceResponse>
        """
        adapter = CASAdapter(
            cas_server_url="https://cas.example.com/cas",
            service_url="https://myapp.example.com/callback",
        )
        result = adapter._parse_cas_response(xml)

        assert result.success is False
        assert result.error_type == CASErrorType.MISSING_USERNAME

    def test_invalid_xml_response(self):
        """Handle malformed XML gracefully."""
        adapter = CASAdapter(
            cas_server_url="https://cas.example.com/cas",
            service_url="https://myapp.example.com/callback",
        )
        result = adapter._parse_cas_response("not valid xml <><>")

        assert result.success is False
        assert result.error_type == CASErrorType.INVALID_RESPONSE

    def test_empty_attributes(self):
        """Handle response with no attributes."""
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <cas:serviceResponse xmlns:cas="http://www.yale.edu/tp/cas">
            <cas:authenticationSuccess>
                <cas:user>jsmith</cas:user>
            </cas:authenticationSuccess>
        </cas:serviceResponse>
        """
        adapter = CASAdapter(
            cas_server_url="https://cas.example.com/cas",
            service_url="https://myapp.example.com/callback",
        )
        result = adapter._parse_cas_response(xml)

        assert result.success is True
        assert result.username == "jsmith"
        assert result.attributes == {}

    def test_username_with_invalid_characters(self):
        """Reject username with invalid characters."""
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <cas:serviceResponse xmlns:cas="http://www.yale.edu/tp/cas">
            <cas:authenticationSuccess>
                <cas:user>user;drop table</cas:user>
            </cas:authenticationSuccess>
        </cas:serviceResponse>
        """
        adapter = CASAdapter(
            cas_server_url="https://cas.example.com/cas",
            service_url="https://myapp.example.com/callback",
        )
        result = adapter._parse_cas_response(xml)

        assert result.success is False
        assert result.error_type == CASErrorType.INVALID_USERNAME


# =============================================================================
# [LAYER: Adapter] [CATEGORY: Failure Mode] Network error handling tests
# =============================================================================

class TestCASValidateTicket:
    """Test CAS ticket validation with mocked HTTP."""

    @patch("webserver.cas_adapter.requests.get")
    def test_successful_validation(self, mock_get):
        """Successful ticket validation flow."""
        mock_response = Mock()
        mock_response.text = """<?xml version="1.0" encoding="UTF-8"?>
        <cas:serviceResponse xmlns:cas="http://www.yale.edu/tp/cas">
            <cas:authenticationSuccess>
                <cas:user>jsmith</cas:user>
                <cas:attributes>
                    <cas:mail>jsmith@slac.stanford.edu</cas:mail>
                </cas:attributes>
            </cas:authenticationSuccess>
        </cas:serviceResponse>
        """
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        adapter = CASAdapter(
            cas_server_url="https://cas.example.com/cas",
            service_url="https://myapp.example.com/callback",
        )
        result = adapter.validate_ticket("ST-123-abc")

        assert result.success is True
        assert result.username == "jsmith"
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert "cas/p3/serviceValidate" in call_args[0][0]
        assert call_args[1]["params"]["ticket"] == "ST-123-abc"

    @patch("webserver.cas_adapter.requests.get")
    def test_network_timeout(self, mock_get):
        """Handle network timeout gracefully."""
        import requests
        mock_get.side_effect = requests.Timeout("Connection timed out")

        adapter = CASAdapter(
            cas_server_url="https://cas.example.com/cas",
            service_url="https://myapp.example.com/callback",
        )
        result = adapter.validate_ticket("ST-123-abc")

        assert result.success is False
        assert result.error_type == CASErrorType.NETWORK_TIMEOUT
        assert "try again" in result.error_message.lower()

    @patch("webserver.cas_adapter.requests.get")
    def test_network_connection_error(self, mock_get):
        """Handle connection error gracefully."""
        import requests
        mock_get.side_effect = requests.ConnectionError("Failed to connect")

        adapter = CASAdapter(
            cas_server_url="https://cas.example.com/cas",
            service_url="https://myapp.example.com/callback",
        )
        result = adapter.validate_ticket("ST-123-abc")

        assert result.success is False
        assert result.error_type == CASErrorType.NETWORK_ERROR


# =============================================================================
# [LAYER: Core] [CATEGORY: Trade-off] Result dataclass tests
# =============================================================================

class TestCASValidationResult:
    """Test the CASValidationResult dataclass."""

    def test_success_result_factory(self):
        """Create success result via factory method."""
        result = CASValidationResult.success_result(
            username="jsmith",
            attributes={"mail": "jsmith@example.com"},
        )
        assert result.success is True
        assert result.username == "jsmith"
        assert result.error_type == CASErrorType.NONE
        assert result.error_message is None

    def test_error_result_factory(self):
        """Create error result via factory method."""
        result = CASValidationResult.error_result(
            error_type=CASErrorType.INVALID_TICKET,
            error_message="Ticket not found",
        )
        assert result.success is False
        assert result.username is None
        assert result.attributes == {}
        assert result.error_type == CASErrorType.INVALID_TICKET

    def test_result_is_immutable(self):
        """Verify result dataclass is frozen (immutable)."""
        result = CASValidationResult.success_result(
            username="jsmith",
            attributes={},
        )
        with pytest.raises(AttributeError):
            result.username = "other"
