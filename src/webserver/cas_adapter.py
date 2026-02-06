"""CAS Protocol Adapter - Pure CAS 3.0 implementation.

This module provides a pure adapter for the CAS (Central Authentication Service)
protocol. It has no Flask dependencies and can be unit tested without I/O mocks.

Boundary: External CAS server (IN)
Contract: CASValidationResult dataclass

Principle #2: Pure Core, Impure Edges - This adapter wraps external I/O.
Principle #3: Boundary Contracts - Explicit CASValidationResult type.
Principle #9: Fail Predictably - All failure modes handled explicitly.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import urlencode, urljoin

import requests

logger = logging.getLogger(__name__)

# Username validation regex - matches RKAuth pattern
USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9@_\-\.]+$")


class CASErrorType(Enum):
    """Types of CAS validation errors for differentiated handling."""

    NONE = "none"
    NETWORK_TIMEOUT = "network_timeout"
    NETWORK_ERROR = "network_error"
    INVALID_TICKET = "invalid_ticket"
    INVALID_RESPONSE = "invalid_response"
    MISSING_USERNAME = "missing_username"
    INVALID_USERNAME = "invalid_username"


@dataclass(frozen=True)
class CASValidationResult:
    """Result of CAS ticket validation.

    This is the boundary contract between the CAS adapter and the application.
    All CAS responses are normalized to this type.

    Attributes:
        success: Whether the ticket was valid and user authenticated.
        username: The authenticated username (None if not successful).
        attributes: CAS attributes (email, displayname, etc.) from the response.
        error_type: The type of error if not successful.
        error_message: Human-readable error message if not successful.
    """

    success: bool
    username: str | None
    attributes: dict[str, str]
    error_type: CASErrorType
    error_message: str | None

    @classmethod
    def success_result(
        cls, username: str, attributes: dict[str, str]
    ) -> CASValidationResult:
        """Create a successful validation result."""
        return cls(
            success=True,
            username=username,
            attributes=attributes,
            error_type=CASErrorType.NONE,
            error_message=None,
        )

    @classmethod
    def error_result(
        cls, error_type: CASErrorType, error_message: str
    ) -> CASValidationResult:
        """Create an error validation result."""
        return cls(
            success=False,
            username=None,
            attributes={},
            error_type=error_type,
            error_message=error_message,
        )


def validate_username(username: str) -> bool:
    """Validate username matches expected pattern.

    CAS usernames should only contain alphanumeric characters and @._-
    This matches the pattern used by RKAuth for consistency.
    """
    return USERNAME_PATTERN.match(username) is not None


def compute_user_attributes(
    cas_username: str,
    cas_attributes: dict[str, str],
    attribute_map: dict[str, str],
    default_email_domain: str = "slac.stanford.edu",
) -> dict[str, Any]:
    """Compute user attributes from CAS response (pure function).

    This is the Core layer logic for computing what user attributes
    should be stored based on CAS response and attribute mapping.

    Args:
        cas_username: Username from CAS.
        cas_attributes: Raw attributes from CAS response.
        attribute_map: Mapping of our field names to CAS attribute names.
            e.g., {"email": "mail", "displayname": "cn"}
        default_email_domain: Domain to use if email not in attributes.

    Returns:
        Dict with 'username', 'email', 'displayname' keys.

    Principle #2: Pure Core - This function has no I/O.
    """
    # Map CAS attributes to our schema
    email_key = attribute_map.get("email", "mail")
    displayname_key = attribute_map.get("displayname", "cn")

    email = cas_attributes.get(email_key)
    if not email:
        email = f"{cas_username}@{default_email_domain}"

    displayname = cas_attributes.get(displayname_key)
    if not displayname:
        displayname = cas_username

    return {
        "username": cas_username,
        "email": email,
        "displayname": displayname,
    }


class CASAdapter:
    """Adapter for CAS 3.0 protocol.

    This class handles communication with a CAS server for authentication.
    It uses the CAS 3.0 protocol (/cas/p3/serviceValidate) which returns
    user attributes in addition to the authentication status.

    Principle #4: Versioned Adapters - This is CASAdapter for CAS 3.0.
    Principle #8: Observe + Adjust - Logs at boundaries with structured fields.

    Example:
        adapter = CASAdapter(
            cas_server_url="https://cas.slac.stanford.edu",
            service_url="https://myapp.slac.stanford.edu/auth/cas/callback"
        )
        result = adapter.validate_ticket(ticket)
        if result.success:
            print(f"Authenticated: {result.username}")
    """

    # CAS 3.0 namespace for XML parsing
    CAS_NS = {"cas": "http://www.yale.edu/tp/cas"}

    def __init__(
        self,
        cas_server_url: str,
        service_url: str,
        timeout: int = 30,
    ) -> None:
        """Initialize CAS adapter.

        Args:
            cas_server_url: Base URL of the CAS server including /cas/ path
                (e.g., https://identity.slac.stanford.edu/cas/).
            service_url: The service URL to register with CAS (callback URL).
            timeout: Timeout in seconds for HTTP requests.
        """
        # Normalize URL (remove trailing slash)
        self.cas_server_url = cas_server_url.rstrip("/")
        self.service_url = service_url
        self.timeout = timeout

    def get_login_url(self, return_url: str | None = None) -> str:
        """Get the CAS login URL.

        Args:
            return_url: Optional URL to redirect to after login.
                If not provided, uses the service_url.

        Returns:
            The full CAS login URL with service parameter.
        """
        service = return_url or self.service_url
        params = urlencode({"service": service})
        return f"{self.cas_server_url}/login?{params}"

    def get_logout_url(self, return_url: str | None = None) -> str:
        """Get the CAS logout URL.

        Args:
            return_url: Optional URL to redirect to after logout.

        Returns:
            The full CAS logout URL.
        """
        base = f"{self.cas_server_url}/logout"
        if return_url:
            params = urlencode({"service": return_url})
            return f"{base}?{params}"
        return base

    def validate_ticket(self, ticket: str) -> CASValidationResult:
        """Validate a CAS ticket and return user information.

        This uses the CAS 3.0 protocol (/cas/p3/serviceValidate) which
        returns user attributes in addition to authentication status.

        Args:
            ticket: The CAS ticket to validate.

        Returns:
            CASValidationResult with success status and user info.

        Principle #9: Fail Predictably - All error paths return explicit results.
        """
        validate_url = f"{self.cas_server_url}/p3/serviceValidate"
        params = {
            "ticket": ticket,
            "service": self.service_url,
        }

        logger.info(
            "CAS validation starting",
            extra={
                "boundary": "cas_adapter",
                "direction": "out",
                "operation": "validate_ticket",
                "cas_server": self.cas_server_url,
            },
        )

        try:
            response = requests.get(
                validate_url,
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()

        except requests.Timeout:
            logger.warning(
                "CAS validation timeout",
                extra={
                    "boundary": "cas_adapter",
                    "direction": "in",
                    "operation": "validate_ticket",
                    "error_type": "timeout",
                },
            )
            return CASValidationResult.error_result(
                CASErrorType.NETWORK_TIMEOUT,
                "CAS server did not respond in time. Please try again.",
            )

        except requests.RequestException as e:
            logger.error(
                "CAS validation network error",
                extra={
                    "boundary": "cas_adapter",
                    "direction": "in",
                    "operation": "validate_ticket",
                    "error_type": "network",
                    "error": str(e),
                },
            )
            return CASValidationResult.error_result(
                CASErrorType.NETWORK_ERROR,
                f"Failed to connect to CAS server: {e}",
            )

        # Parse the CAS response
        return self._parse_cas_response(response.text)

    def _parse_cas_response(self, xml_text: str) -> CASValidationResult:
        """Parse CAS XML response.

        CAS 3.0 response format:
        <cas:serviceResponse>
            <cas:authenticationSuccess>
                <cas:user>username</cas:user>
                <cas:attributes>
                    <cas:mail>user@example.com</cas:mail>
                    <cas:cn>Display Name</cas:cn>
                </cas:attributes>
            </cas:authenticationSuccess>
        </cas:serviceResponse>

        Or on failure:
        <cas:serviceResponse>
            <cas:authenticationFailure code="INVALID_TICKET">
                Ticket not recognized
            </cas:authenticationFailure>
        </cas:serviceResponse>
        """
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.error(
                "CAS response XML parse error",
                extra={
                    "boundary": "cas_adapter",
                    "direction": "in",
                    "operation": "parse_response",
                    "error": str(e),
                },
            )
            return CASValidationResult.error_result(
                CASErrorType.INVALID_RESPONSE,
                "Invalid response from CAS server.",
            )

        # Check for authentication success
        success_elem = root.find("cas:authenticationSuccess", self.CAS_NS)
        if success_elem is not None:
            return self._parse_success_response(success_elem)

        # Check for authentication failure
        failure_elem = root.find("cas:authenticationFailure", self.CAS_NS)
        if failure_elem is not None:
            code = failure_elem.get("code", "UNKNOWN")
            message = (failure_elem.text or "Authentication failed").strip()
            logger.info(
                "CAS authentication failed",
                extra={
                    "boundary": "cas_adapter",
                    "direction": "in",
                    "operation": "validate_ticket",
                    "cas_error_code": code,
                },
            )
            return CASValidationResult.error_result(
                CASErrorType.INVALID_TICKET,
                f"CAS authentication failed: {message}",
            )

        # Unknown response format
        logger.error(
            "CAS response format not recognized",
            extra={
                "boundary": "cas_adapter",
                "direction": "in",
                "operation": "parse_response",
            },
        )
        return CASValidationResult.error_result(
            CASErrorType.INVALID_RESPONSE,
            "Unexpected response format from CAS server.",
        )

    def _parse_success_response(self, success_elem: ET.Element) -> CASValidationResult:
        """Parse a successful CAS authentication response."""
        # Extract username
        user_elem = success_elem.find("cas:user", self.CAS_NS)
        if user_elem is None or not user_elem.text:
            logger.error(
                "CAS response missing username",
                extra={
                    "boundary": "cas_adapter",
                    "direction": "in",
                    "operation": "parse_response",
                },
            )
            return CASValidationResult.error_result(
                CASErrorType.MISSING_USERNAME,
                "CAS response did not include a username.",
            )

        username = user_elem.text.strip()

        # Validate username format
        if not validate_username(username):
            logger.error(
                "CAS username failed validation",
                extra={
                    "boundary": "cas_adapter",
                    "direction": "in",
                    "operation": "parse_response",
                    "username_length": len(username),
                },
            )
            return CASValidationResult.error_result(
                CASErrorType.INVALID_USERNAME,
                "CAS username contains invalid characters.",
            )

        # Extract attributes
        attributes: dict[str, str] = {}
        attrs_elem = success_elem.find("cas:attributes", self.CAS_NS)
        if attrs_elem is not None:
            for child in attrs_elem:
                # Remove namespace prefix from tag
                tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                if child.text:
                    attributes[tag] = child.text.strip()

        logger.info(
            "CAS validation successful",
            extra={
                "boundary": "cas_adapter",
                "direction": "in",
                "operation": "validate_ticket",
                "username": username,
                "attribute_count": len(attributes),
            },
        )

        return CASValidationResult.success_result(username, attributes)
