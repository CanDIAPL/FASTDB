"""CAS Flask Blueprint - Flask integration for CAS authentication.

This module provides Flask routes for CAS authentication, including login,
callback (ticket validation), and logout endpoints.

Boundary: Flask HTTP (IN) -> CAS Adapter (OUT) -> PostgreSQL (OUT)
Contract: Sets same session variables as RKAuth for compatibility.

Principle #2: Pure Core, Impure Edges - Uses CAS adapter for protocol logic.
Principle #5: High Cohesion - Only handles CAS authentication flow.
Principle #8: Observe + Adjust - Logs at all boundaries.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

import flask
import psycopg
import psycopg.rows

from webserver.cas_adapter import (
    CASAdapter,
    CASErrorType,
    CASValidationResult,
    compute_user_attributes,
)

logger = logging.getLogger(__name__)

bp = flask.Blueprint("cas", __name__, url_prefix="/auth/cas")


@dataclass
class CASConfig:
    """Configuration for CAS authentication.

    This class holds the CAS configuration and is set via setparams()
    before the Flask app registers the blueprint.

    Principle #6: Composable + Extensible - Configuration is injected.
    """

    cas_server_url: str = ""
    cas_service_url: str | None = None
    auto_create_users: bool = True
    attribute_map: dict[str, str] = field(
        default_factory=lambda: {"email": "mail", "displayname": "cn"}
    )
    default_email_domain: str = "slac.stanford.edu"

    # Database configuration
    db_host: str = "postgres"
    db_port: int = 5432
    db_name: str = "fastdb"
    db_user: str = "postgres"
    db_password: str = ""

    # Group support (matches RKAuth)
    use_groups: bool = False
    authuser_table: str = "authuser"
    authgroup_table: str = "authgroup"
    auth_user_group_table: str = "auth_user_group"

    @classmethod
    def setparams(cls, **kwargs: Any) -> None:
        """Set configuration parameters.

        This follows the same pattern as RKAuthConfig.setdbparams().
        """
        for key, val in kwargs.items():
            if not hasattr(_config, key):
                raise AttributeError(f"CASConfig: unknown attribute {key}")
            setattr(_config, key, val)


# Global config instance (matches RKAuth pattern)
_config = CASConfig()


@contextlib.contextmanager
def _db_connection():
    """Context manager for database connections.

    Principle #9: Fail Predictably - Connection errors are logged and re-raised.
    """
    conn = None
    try:
        conn = psycopg.connect(
            host=_config.db_host,
            port=_config.db_port,
            dbname=_config.db_name,
            user=_config.db_user,
            password=_config.db_password,
            row_factory=psycopg.rows.dict_row,
        )
        yield conn
    finally:
        if conn is not None:
            conn.close()


class UserRepository:
    """Repository for authuser database operations.

    This is the Adapter layer for database access, separating I/O from logic.

    Principle #2: Pure Core, Impure Edges - All DB I/O is in this class.
    """

    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn
        self.table = _config.authuser_table

    def find_by_username(self, username: str) -> dict | None:
        """Find a user by username."""
        cursor = self.conn.cursor()
        cursor.execute(
            f"SELECT id, username, displayname, email FROM {self.table} "
            "WHERE username = %(username)s",
            {"username": username},
        )
        rows = cursor.fetchall()
        cursor.close()
        if len(rows) == 0:
            return None
        return rows[0]

    def create_user(
        self, username: str, email: str, displayname: str
    ) -> dict:
        """Create a new user (CAS users have no pubkey/privkey)."""
        user_id = uuid.uuid4()
        cursor = self.conn.cursor()
        cursor.execute(
            f"INSERT INTO {self.table} (id, username, displayname, email, pubkey, privkey) "
            "VALUES (%(id)s, %(username)s, %(displayname)s, %(email)s, NULL, NULL) "
            "RETURNING id, username, displayname, email",
            {
                "id": str(user_id),
                "username": username,
                "displayname": displayname,
                "email": email,
            },
        )
        result = cursor.fetchone()
        self.conn.commit()
        cursor.close()

        logger.info(
            "Created new CAS user",
            extra={
                "boundary": "user_repository",
                "direction": "out",
                "operation": "create_user",
                "username": username,
            },
        )

        return result

    def update_user(
        self, user_id: str, email: str, displayname: str
    ) -> dict:
        """Update an existing user's email and displayname."""
        cursor = self.conn.cursor()
        cursor.execute(
            f"UPDATE {self.table} SET email = %(email)s, displayname = %(displayname)s "
            "WHERE id = %(id)s "
            "RETURNING id, username, displayname, email",
            {"id": user_id, "email": email, "displayname": displayname},
        )
        result = cursor.fetchone()
        self.conn.commit()
        cursor.close()

        logger.info(
            "Updated CAS user attributes",
            extra={
                "boundary": "user_repository",
                "direction": "out",
                "operation": "update_user",
                "user_id": str(user_id),
            },
        )

        return result

    def get_user_groups(self, user_id: str) -> list[str]:
        """Get groups for a user (if groups are enabled)."""
        if not _config.use_groups:
            return []

        cursor = self.conn.cursor()
        cursor.execute(
            f"SELECT g.name FROM {_config.authgroup_table} g "
            f"INNER JOIN {_config.auth_user_group_table} aug ON g.id = aug.groupid "
            "WHERE aug.userid = %(userid)s",
            {"userid": user_id},
        )
        rows = cursor.fetchall()
        cursor.close()
        return [row["name"] for row in rows]


def _get_cas_adapter() -> CASAdapter:
    """Create a CAS adapter with current configuration.

    Auto-detects service URL if not configured.
    """
    service_url = _config.cas_service_url
    if not service_url:
        # Auto-detect from current request
        service_url = flask.url_for("cas.cas_callback", _external=True)

    return CASAdapter(
        cas_server_url=_config.cas_server_url,
        service_url=service_url,
    )


def _set_session_from_user(user: dict, groups: list[str]) -> None:
    """Set Flask session variables from user data.

    Sets the same session variables as RKAuth for compatibility:
    - authenticated: True
    - username: user's username
    - useruuid: user's database UUID
    - userdisplayname: user's display name
    - useremail: user's email
    - usergroups: list of group names

    Principle #3: Boundary Contracts - Same session contract as RKAuth.
    """
    flask.session["authenticated"] = True
    flask.session["username"] = user["username"]
    flask.session["useruuid"] = user["id"]
    flask.session["userdisplayname"] = user["displayname"]
    flask.session["useremail"] = user["email"]
    flask.session["usergroups"] = groups


def _provision_or_update_user(
    cas_username: str, cas_attributes: dict[str, str], repo: UserRepository
) -> tuple[dict, list[str]]:
    """Provision a new user or update existing user from CAS attributes.

    Returns:
        Tuple of (user_dict, groups_list)

    Raises:
        PermissionError: If user doesn't exist and auto_create is False.
    """
    # Compute user attributes using pure function
    computed = compute_user_attributes(
        cas_username,
        cas_attributes,
        _config.attribute_map,
        _config.default_email_domain,
    )

    existing_user = repo.find_by_username(cas_username)

    if existing_user is None:
        if not _config.auto_create_users:
            logger.warning(
                "CAS user not found and auto-create disabled",
                extra={
                    "boundary": "cas_flask",
                    "direction": "in",
                    "operation": "provision_user",
                    "username": cas_username,
                },
            )
            raise PermissionError(
                f"User {cas_username} is not registered in this system."
            )

        # Create new user
        user = repo.create_user(
            username=computed["username"],
            email=computed["email"],
            displayname=computed["displayname"],
        )
        groups = []  # New user has no groups
    else:
        user = existing_user
        # Update attributes if they changed
        if (
            user["email"] != computed["email"]
            or user["displayname"] != computed["displayname"]
        ):
            user = repo.update_user(
                user_id=str(user["id"]),
                email=computed["email"],
                displayname=computed["displayname"],
            )
        groups = repo.get_user_groups(str(user["id"]))

    return user, groups


def _error_response(
    result: CASValidationResult,
) -> tuple[str, int]:
    """Generate appropriate HTTP response for CAS errors.

    Principle #9: Fail Predictably - Differentiated error handling.
    """
    status_map = {
        CASErrorType.NETWORK_TIMEOUT: (
            "The authentication server did not respond. Please try again.",
            503,
        ),
        CASErrorType.NETWORK_ERROR: (
            "Could not connect to authentication server. Please try again later.",
            503,
        ),
        CASErrorType.INVALID_TICKET: (
            "Authentication failed. Please log in again.",
            401,
        ),
        CASErrorType.INVALID_RESPONSE: (
            "Authentication server returned an invalid response.",
            502,
        ),
        CASErrorType.MISSING_USERNAME: (
            "Authentication server did not provide a username.",
            502,
        ),
        CASErrorType.INVALID_USERNAME: (
            "Username contains invalid characters.",
            400,
        ),
    }

    message, status = status_map.get(
        result.error_type,
        ("Authentication failed.", 401),
    )

    return message, status


@bp.route("/login", methods=["GET"])
def cas_login():
    """Redirect to SLAC CAS login page.

    Stores the original URL in session for post-login redirect.

    Query parameters:
        next: Optional URL to redirect to after successful login.

    Returns:
        302 redirect to CAS login page.
    """
    # Store the URL to redirect to after login
    next_url = flask.request.args.get("next")
    if next_url:
        flask.session["cas_next_url"] = next_url
    else:
        # Default to referring page or root
        flask.session["cas_next_url"] = flask.request.referrer or "/"

    adapter = _get_cas_adapter()
    login_url = adapter.get_login_url()

    logger.info(
        "Redirecting to CAS login",
        extra={
            "boundary": "cas_flask",
            "direction": "out",
            "operation": "login_redirect",
        },
    )

    return flask.redirect(login_url)


@bp.route("/callback", methods=["GET"])
def cas_callback():
    """Handle CAS callback with ticket validation.

    CAS redirects here after successful authentication with a ticket
    parameter. We validate the ticket, provision/update the user,
    and set up the session.

    Query parameters:
        ticket: The CAS ticket to validate.

    Returns:
        302 redirect to original URL on success.
        4xx/5xx error page on failure.
    """
    ticket = flask.request.args.get("ticket")

    if not ticket:
        logger.warning(
            "CAS callback without ticket",
            extra={
                "boundary": "cas_flask",
                "direction": "in",
                "operation": "callback",
            },
        )
        return "No ticket provided. Please try logging in again.", 400

    adapter = _get_cas_adapter()
    result = adapter.validate_ticket(ticket)

    if not result.success:
        return _error_response(result)

    # Provision or update user
    try:
        with _db_connection() as conn:
            repo = UserRepository(conn)
            user, groups = _provision_or_update_user(
                result.username, result.attributes, repo
            )
    except PermissionError as e:
        logger.warning(
            "CAS user not authorized",
            extra={
                "boundary": "cas_flask",
                "direction": "in",
                "operation": "callback",
                "username": result.username,
            },
        )
        return str(e), 403
    except Exception as e:
        logger.exception(
            "Database error during CAS callback",
            extra={
                "boundary": "cas_flask",
                "direction": "in",
                "operation": "callback",
            },
        )
        return f"Internal error during authentication: {e}", 500

    # Set session variables (same as RKAuth)
    _set_session_from_user(user, groups)

    logger.info(
        "CAS login successful",
        extra={
            "boundary": "cas_flask",
            "direction": "in",
            "operation": "callback",
            "username": user["username"],
            "is_new_user": len(groups) == 0 and not _config.use_groups,
        },
    )

    # Redirect to the originally requested URL
    next_url = flask.session.pop("cas_next_url", "/")
    return flask.redirect(next_url)


@bp.route("/logout", methods=["GET", "POST"])
def cas_logout():
    """Log out of the application and optionally CAS.

    Clears the session and optionally redirects to CAS logout.

    Query parameters:
        cas_logout: If "true", also log out of CAS (single logout).
        next: URL to redirect to after logout.

    Returns:
        JSON response or redirect depending on parameters.
    """
    # Clear session
    flask.session["authenticated"] = False
    for key in ["username", "useruuid", "useremail", "userdisplayname", "usergroups"]:
        flask.session.pop(key, None)

    logger.info(
        "CAS logout",
        extra={
            "boundary": "cas_flask",
            "direction": "in",
            "operation": "logout",
        },
    )

    # Check if we should also log out of CAS
    cas_logout = flask.request.args.get("cas_logout", "false").lower() == "true"
    next_url = flask.request.args.get("next")

    if cas_logout:
        adapter = _get_cas_adapter()
        logout_url = adapter.get_logout_url(return_url=next_url)
        return flask.redirect(logout_url)

    if next_url:
        return flask.redirect(next_url)

    return flask.jsonify({"status": "Logged out"})


@bp.route("/isauth", methods=["POST"])
def cas_isauth():
    """Check if user is authenticated.

    This matches the RKAuth /auth/isauth endpoint for compatibility.

    Returns:
        JSON with authentication status and user info.
    """
    if flask.session.get("authenticated"):
        return flask.jsonify(
            {
                "status": True,
                "username": flask.session.get("username"),
                "useruuid": str(flask.session.get("useruuid")),
                "useremail": flask.session.get("useremail"),
                "userdisplayname": flask.session.get("userdisplayname"),
                "usergroups": flask.session.get("usergroups", []),
            }
        )
    else:
        return flask.jsonify({"status": False})
