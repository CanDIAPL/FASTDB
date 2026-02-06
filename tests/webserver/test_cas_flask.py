"""Integration tests for CAS Flask blueprint.

These tests verify the Flask integration for CAS authentication,
including callback handling, user provisioning, and session management.

Layer: Adapter
Category: Integration, Failure Mode, Realistic Scenario
"""

import uuid
import pytest
from unittest.mock import Mock, patch, MagicMock

import flask

from webserver.cas_flask import (
    bp,
    CASConfig,
    UserRepository,
    _set_session_from_user,
    _provision_or_update_user,
    _error_response,
)
from webserver.cas_adapter import CASErrorType, CASValidationResult


# =============================================================================
# Flask test app fixture
# =============================================================================

@pytest.fixture
def app():
    """Create a Flask test app with CAS blueprint."""
    test_app = flask.Flask(__name__)
    test_app.config["TESTING"] = True
    test_app.config["SECRET_KEY"] = "test-secret-key"
    test_app.register_blueprint(bp)

    # Configure CAS
    CASConfig.setparams(
        cas_server_url="https://cas.test.example.com",
        cas_service_url="https://myapp.test.example.com/auth/cas/callback",
        auto_create_users=True,
        attribute_map={"email": "mail", "displayname": "cn"},
        default_email_domain="test.example.com",
        db_host="localhost",
        db_port=5432,
        db_name="testdb",
        db_user="testuser",
        db_password="testpass",
    )

    return test_app


@pytest.fixture
def client(app):
    """Create a test client."""
    return app.test_client()


# =============================================================================
# [LAYER: Adapter] [CATEGORY: Specification] Session management tests
# =============================================================================

class TestSessionManagement:
    """Test session variable handling."""

    def test_set_session_from_user(self, app):
        """Verify session variables are set correctly."""
        with app.test_request_context():
            user = {
                "id": uuid.uuid4(),
                "username": "jsmith",
                "displayname": "John Smith",
                "email": "jsmith@example.com",
            }
            groups = ["admin", "users"]

            _set_session_from_user(user, groups)

            assert flask.session["authenticated"] is True
            assert flask.session["username"] == "jsmith"
            assert flask.session["useruuid"] == user["id"]
            assert flask.session["userdisplayname"] == "John Smith"
            assert flask.session["useremail"] == "jsmith@example.com"
            assert flask.session["usergroups"] == ["admin", "users"]


# =============================================================================
# [LAYER: Adapter] [CATEGORY: Failure Mode] Error response tests
# =============================================================================

class TestErrorResponses:
    """Test error response generation."""

    def test_timeout_returns_503(self):
        """Network timeout returns 503 Service Unavailable."""
        result = CASValidationResult.error_result(
            CASErrorType.NETWORK_TIMEOUT,
            "Timeout occurred",
        )
        message, status = _error_response(result)
        assert status == 503
        assert "try again" in message.lower()

    def test_network_error_returns_503(self):
        """Network error returns 503 Service Unavailable."""
        result = CASValidationResult.error_result(
            CASErrorType.NETWORK_ERROR,
            "Connection failed",
        )
        message, status = _error_response(result)
        assert status == 503

    def test_invalid_ticket_returns_401(self):
        """Invalid ticket returns 401 Unauthorized."""
        result = CASValidationResult.error_result(
            CASErrorType.INVALID_TICKET,
            "Ticket not recognized",
        )
        message, status = _error_response(result)
        assert status == 401
        assert "log in again" in message.lower()

    def test_invalid_response_returns_502(self):
        """Invalid CAS response returns 502 Bad Gateway."""
        result = CASValidationResult.error_result(
            CASErrorType.INVALID_RESPONSE,
            "Malformed XML",
        )
        message, status = _error_response(result)
        assert status == 502


# =============================================================================
# [LAYER: Adapter] [CATEGORY: Integration] Route tests
# =============================================================================

class TestLoginRoute:
    """Test /auth/cas/login route."""

    def test_login_redirects_to_cas(self, client):
        """Login route redirects to CAS login page."""
        response = client.get("/auth/cas/login")
        assert response.status_code == 302
        assert "cas.test.example.com/cas/login" in response.location
        assert "service=" in response.location

    def test_login_stores_next_url(self, client):
        """Login route stores next URL in session."""
        with client.session_transaction() as sess:
            pass  # clear session

        response = client.get("/auth/cas/login?next=/dashboard")
        assert response.status_code == 302

        with client.session_transaction() as sess:
            assert sess.get("cas_next_url") == "/dashboard"


class TestCallbackRoute:
    """Test /auth/cas/callback route."""

    def test_callback_without_ticket(self, client):
        """Callback without ticket returns 400."""
        response = client.get("/auth/cas/callback")
        assert response.status_code == 400
        assert b"No ticket" in response.data

    @patch("webserver.cas_flask._get_cas_adapter")
    @patch("webserver.cas_flask._db_connection")
    def test_callback_success_creates_session(
        self, mock_db_conn, mock_get_adapter, client
    ):
        """Successful callback creates authenticated session."""
        # Mock CAS adapter
        mock_adapter = Mock()
        mock_adapter.validate_ticket.return_value = CASValidationResult.success_result(
            username="jsmith",
            attributes={"mail": "jsmith@test.com", "cn": "John Smith"},
        )
        mock_get_adapter.return_value = mock_adapter

        # Mock database connection and user repository
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        # User doesn't exist yet
        mock_cursor.fetchall.side_effect = [
            [],  # find_by_username returns empty
            [{"id": uuid.uuid4(), "username": "jsmith",
              "displayname": "John Smith", "email": "jsmith@test.com"}],  # create returns user
        ]
        mock_cursor.fetchone.return_value = {
            "id": uuid.uuid4(),
            "username": "jsmith",
            "displayname": "John Smith",
            "email": "jsmith@test.com",
        }
        mock_db_conn.return_value.__enter__ = Mock(return_value=mock_conn)
        mock_db_conn.return_value.__exit__ = Mock(return_value=False)

        response = client.get("/auth/cas/callback?ticket=ST-123-abc")

        # Should redirect to home after successful login
        assert response.status_code == 302

        # Check session was set
        with client.session_transaction() as sess:
            assert sess.get("authenticated") is True
            assert sess.get("username") == "jsmith"

    @patch("webserver.cas_flask._get_cas_adapter")
    def test_callback_invalid_ticket(self, mock_get_adapter, client):
        """Invalid ticket returns 401."""
        mock_adapter = Mock()
        mock_adapter.validate_ticket.return_value = CASValidationResult.error_result(
            CASErrorType.INVALID_TICKET,
            "Ticket not recognized",
        )
        mock_get_adapter.return_value = mock_adapter

        response = client.get("/auth/cas/callback?ticket=ST-invalid")
        assert response.status_code == 401


class TestLogoutRoute:
    """Test /auth/cas/logout route."""

    def test_logout_clears_session(self, client, app):
        """Logout clears authentication session."""
        # First set up an authenticated session
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["username"] = "jsmith"
            sess["useruuid"] = str(uuid.uuid4())
            sess["userdisplayname"] = "John Smith"
            sess["useremail"] = "jsmith@example.com"
            sess["usergroups"] = []

        response = client.get("/auth/cas/logout")
        assert response.status_code == 200

        with client.session_transaction() as sess:
            assert sess.get("authenticated") is False
            assert sess.get("username") is None

    def test_logout_with_cas_logout(self, client):
        """Logout with cas_logout redirects to CAS logout."""
        response = client.get("/auth/cas/logout?cas_logout=true")
        assert response.status_code == 302
        assert "cas.test.example.com/cas/logout" in response.location

    def test_logout_with_next_url(self, client):
        """Logout with next URL redirects there."""
        response = client.get("/auth/cas/logout?next=/home")
        assert response.status_code == 302
        assert response.location == "/home"


class TestIsAuthRoute:
    """Test /auth/cas/isauth route."""

    def test_isauth_when_authenticated(self, client):
        """Returns user info when authenticated."""
        user_id = uuid.uuid4()
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["username"] = "jsmith"
            sess["useruuid"] = user_id
            sess["userdisplayname"] = "John Smith"
            sess["useremail"] = "jsmith@example.com"
            sess["usergroups"] = ["users"]

        response = client.post("/auth/cas/isauth")
        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] is True
        assert data["username"] == "jsmith"
        assert data["userdisplayname"] == "John Smith"

    def test_isauth_when_not_authenticated(self, client):
        """Returns status False when not authenticated."""
        response = client.post("/auth/cas/isauth")
        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] is False


# =============================================================================
# [LAYER: Adapter] [CATEGORY: Integration] User provisioning tests
# =============================================================================

class TestUserProvisioning:
    """Test user provisioning logic."""

    def test_provision_creates_new_user(self, app):
        """Creates new user when not found."""
        with app.app_context():
            mock_repo = Mock(spec=UserRepository)
            mock_repo.find_by_username.return_value = None
            new_user = {
                "id": uuid.uuid4(),
                "username": "newuser",
                "email": "newuser@test.example.com",
                "displayname": "New User",
            }
            mock_repo.create_user.return_value = new_user
            mock_repo.get_user_groups.return_value = []

            user, groups = _provision_or_update_user(
                "newuser",
                {"mail": "newuser@test.example.com", "cn": "New User"},
                mock_repo,
            )

            mock_repo.create_user.assert_called_once()
            assert user["username"] == "newuser"
            assert groups == []

    def test_provision_updates_existing_user(self, app):
        """Updates existing user when attributes changed."""
        with app.app_context():
            mock_repo = Mock(spec=UserRepository)
            existing_user = {
                "id": uuid.uuid4(),
                "username": "existinguser",
                "email": "old@example.com",
                "displayname": "Old Name",
            }
            mock_repo.find_by_username.return_value = existing_user
            updated_user = {
                "id": existing_user["id"],
                "username": "existinguser",
                "email": "new@example.com",
                "displayname": "New Name",
            }
            mock_repo.update_user.return_value = updated_user
            mock_repo.get_user_groups.return_value = ["admins"]

            user, groups = _provision_or_update_user(
                "existinguser",
                {"mail": "new@example.com", "cn": "New Name"},
                mock_repo,
            )

            mock_repo.update_user.assert_called_once()
            assert user["email"] == "new@example.com"
            assert groups == ["admins"]

    def test_provision_no_update_when_unchanged(self, app):
        """Does not update when attributes unchanged."""
        with app.app_context():
            mock_repo = Mock(spec=UserRepository)
            existing_user = {
                "id": uuid.uuid4(),
                "username": "existinguser",
                "email": "same@test.example.com",
                "displayname": "Same Name",
            }
            mock_repo.find_by_username.return_value = existing_user
            mock_repo.get_user_groups.return_value = []

            user, groups = _provision_or_update_user(
                "existinguser",
                {"mail": "same@test.example.com", "cn": "Same Name"},
                mock_repo,
            )

            mock_repo.update_user.assert_not_called()
            assert user == existing_user

    def test_provision_fails_when_auto_create_disabled(self, app):
        """Raises PermissionError when auto_create disabled and user not found."""
        # Temporarily disable auto create
        original = CASConfig()
        CASConfig.setparams(auto_create_users=False)

        try:
            with app.app_context():
                mock_repo = Mock(spec=UserRepository)
                mock_repo.find_by_username.return_value = None

                with pytest.raises(PermissionError) as exc_info:
                    _provision_or_update_user("unknownuser", {}, mock_repo)

                assert "not registered" in str(exc_info.value)
        finally:
            CASConfig.setparams(auto_create_users=True)


# =============================================================================
# [LAYER: Adapter] [CATEGORY: Realistic Scenario] Config tests
# =============================================================================

class TestCASConfig:
    """Test CAS configuration."""

    def test_setparams_valid_attributes(self):
        """Setting valid config attributes works."""
        CASConfig.setparams(
            cas_server_url="https://new.cas.example.com",
        )
        from webserver.cas_flask import _config
        assert _config.cas_server_url == "https://new.cas.example.com"

    def test_setparams_invalid_attribute(self):
        """Setting invalid config attribute raises error."""
        with pytest.raises(AttributeError):
            CASConfig.setparams(invalid_attribute="value")
