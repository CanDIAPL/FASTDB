# ADR: CAS Authentication (Replacing RKAuth as Default)

**Status:** Proposed
**Date:** 2026-02-06
**Author:** Carlo Costantini

## Context

FASTDB currently uses RKAuth (Rubin-Kerberos Authentication) for user authentication. RKAuth is a custom challenge-response system using RSA-4096 and AES-256-GCM cryptography, implemented in `extern/rkwebutil/rkwebutil/rkauth_flask.py`.

### Current RKAuth System

1. **Challenge-Response Protocol**: Server sends encrypted challenge; client decrypts with password-derived key
2. **No Server-Side Passwords**: Only public keys stored; private keys encrypted client-side
3. **Session-Based**: Flask-Session with filesystem backend
4. **Database Tables**: `authuser` (credentials), `passwordlink` (password reset)

### Limitations of RKAuth for SLAC Deployment

1. **No SSO Integration**: Users must maintain separate FASTDB credentials
2. **Password Management Burden**: Password reset requires email infrastructure
3. **Onboarding Friction**: New users need account creation workflow
4. **Security Considerations**: Independent password database is an additional attack surface

### SLAC CAS (Central Authentication Service)

SLAC provides institutional SSO via CAS protocol. Benefits:

1. **Single Sign-On**: Users authenticate once for all SLAC services
2. **No Password Storage**: FASTDB never sees credentials
3. **Automatic Provisioning**: Users already have SLAC accounts
4. **Centralized Security**: Authentication managed by security team

## Decision

Implement SLAC CAS as the primary authentication method while keeping RKAuth available as a configurable fallback. Auto-provision users from CAS attributes on first login.

### Design Principles

1. **Configurable Per Deployment**: `auth_method` setting selects "cas", "rkauth", or "both"
2. **Session Compatibility**: CAS sets identical session variables as RKAuth
3. **Auto-Provisioning**: New users created from CAS attributes (username, email, displayname)
4. **Zero Database Migration**: Existing schema supports CAS users (NULL pubkey/privkey)
5. **Fail-Safe**: CAS errors return clear messages; RKAuth fallback available

### Authentication Methods by Deployment

| Deployment | auth_method | RKAuth | CAS | Primary Use |
|------------|-------------|--------|-----|-------------|
| SLAC Production | "cas" | No | Yes | All users via SSO |
| SLAC Development | "both" | Yes | Yes | Testing + service accounts |
| External/Self-hosted | "rkauth" | Yes | No | Independent deployments |

## Detailed Design

### 1. CAS Protocol Adapter

Pure CAS protocol implementation without Flask dependencies:

```python
# src/webserver/cas_adapter.py

@dataclass
class CASValidationResult:
    success: bool
    username: str | None
    attributes: dict[str, str]  # email, displayname, groups
    error: str | None

class CASAdapter:
    """CAS 3.0 protocol adapter (I/O boundary)."""

    def __init__(self, cas_server_url: str, service_url: str, timeout: int = 30):
        self.cas_server_url = cas_server_url.rstrip('/')
        self.service_url = service_url
        self.timeout = timeout

    def get_login_url(self, return_url: str | None = None) -> str:
        """Build CAS login redirect URL."""
        # https://cas.slac.stanford.edu/cas/login?service=<callback>

    def validate_ticket(self, ticket: str) -> CASValidationResult:
        """Validate CAS ticket via /cas/p3/serviceValidate endpoint."""
        # Returns XML with username and attributes

    def get_logout_url(self, return_url: str | None = None) -> str:
        """Build CAS logout redirect URL."""
```

**Boundary Contract**: The adapter validates external CAS responses and returns typed `CASValidationResult`. Flask blueprint depends on this contract, not raw XML.

### 2. Flask Blueprint

```python
# src/webserver/cas_flask.py

class CASConfig:
    """Global CAS configuration (mirrors RKAuthConfig pattern)."""
    cas_server_url: str = None
    cas_service_url: str = None
    auto_create_users: bool = True
    attribute_map: dict = {"email": "mail", "displayname": "cn"}

    @classmethod
    def setparams(cls, **kwargs): ...

bp = flask.Blueprint('cas', __name__, url_prefix='/auth/cas')

@bp.route('/login', methods=['GET'])
def cas_login():
    """Redirect to SLAC CAS login with service callback URL."""
    flask.session['cas_return_url'] = flask.request.args.get('next', '/')
    return flask.redirect(adapter.get_login_url())

@bp.route('/callback', methods=['GET'])
def cas_callback():
    """Handle CAS ticket validation and user provisioning."""
    ticket = flask.request.args.get('ticket')
    result = adapter.validate_ticket(ticket)

    if not result.success:
        return "CAS authentication failed", 401

    user = provision_or_update_user(result.username, result.attributes)

    # Set session variables (identical to RKAuth)
    flask.session['authenticated'] = True
    flask.session['username'] = user.username
    flask.session['useruuid'] = str(user.id)
    flask.session['userdisplayname'] = user.displayname
    flask.session['useremail'] = user.email
    flask.session['usergroups'] = result.attributes.get('groups', [])

    return flask.redirect(flask.session.pop('cas_return_url', '/'))

@bp.route('/logout', methods=['GET', 'POST'])
def cas_logout():
    """Clear session and optionally redirect to CAS logout."""
    flask.session.clear()
    return flask.redirect(adapter.get_logout_url())
```

### 3. Configuration

```python
# src/config.py (additions)

import os

# Authentication method: "cas", "rkauth", or "both"
auth_method = os.getenv("FASTDB_AUTH_METHOD", "cas")

# CAS configuration
cas_server_url = os.getenv("FASTDB_CAS_SERVER_URL", "https://cas.slac.stanford.edu")
cas_service_url = os.getenv("FASTDB_CAS_SERVICE_URL")  # Auto-detect if None
cas_auto_create_users = os.getenv("FASTDB_CAS_AUTO_CREATE_USERS", "true").lower() == "true"
cas_attribute_map = {
    "email": "mail",
    "displayname": "cn",
}
```

### 4. Server Registration

```python
# src/webserver/server.py (modified lines 251-266)

import webserver.cas_flask as cas_flask

# Register authentication blueprints based on config
if config.auth_method in ("rkauth", "both"):
    rkauth_flask.RKAuthConfig.setdbparams(...)
    app.register_blueprint(rkauth_flask.bp)

if config.auth_method in ("cas", "both"):
    cas_flask.CASConfig.setparams(
        cas_server_url=config.cas_server_url,
        cas_service_url=config.cas_service_url,
        auto_create_users=config.cas_auto_create_users,
        attribute_map=config.cas_attribute_map,
        db_host=db.dbhost,
        db_port=db.dbport,
        db_name=db.dbname,
        db_user=db.dbuser,
        db_password=db.dbpasswd,
    )
    app.register_blueprint(cas_flask.bp)
```

### 5. User Provisioning

```python
def provision_or_update_user(cas_username: str, cas_attrs: dict) -> AuthUser:
    """Create or update user from CAS attributes.

    CAS users have pubkey=NULL and privkey=NULL (no password authentication).
    """
    with DBCon() as con:
        rows, _ = con.execute(
            "SELECT * FROM authuser WHERE username=%(u)s", {'u': cas_username}
        )

        email = cas_attrs.get('email', f'{cas_username}@slac.stanford.edu')
        displayname = cas_attrs.get('displayname', cas_username)

        if len(rows) == 0:
            # Create new CAS user
            user = AuthUser(
                username=cas_username,
                email=email,
                displayname=displayname,
                pubkey=None,
                privkey=None
            )
            user.insert(dbcon=con)
        else:
            # Update existing user if CAS attributes changed
            user = AuthUser(cols=..., vals=rows[0])
            if user.email != email or user.displayname != displayname:
                user.email = email
                user.displayname = displayname
                user.update(dbcon=con)

        return user
```

## Schema

No migration required. The existing `authuser` table already supports CAS-only users:

```sql
CREATE TABLE authuser(
  id UUID NOT NULL DEFAULT gen_random_uuid(),
  username text NOT NULL,
  displayname text NOT NULL,
  email text NOT NULL,
  pubkey text,      -- NULL for CAS-only users
  privkey jsonb     -- NULL for CAS-only users
);
CREATE UNIQUE INDEX ix_authuser_username ON authuser USING btree (username);
```

## Authentication Flow

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Browser   │     │   FASTDB    │     │  SLAC CAS   │     │  Database   │
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       │                   │                   │                   │
       │ GET /protected    │                   │                   │
       │──────────────────>│                   │                   │
       │                   │                   │                   │
       │ 302 /auth/cas/login                   │                   │
       │<──────────────────│                   │                   │
       │                   │                   │                   │
       │ 302 CAS login?service=callback        │                   │
       │──────────────────────────────────────>│                   │
       │                   │                   │                   │
       │           [User authenticates]        │                   │
       │                   │                   │                   │
       │ 302 callback?ticket=ST-xxx            │                   │
       │<──────────────────────────────────────│                   │
       │                   │                   │                   │
       │ GET /auth/cas/callback?ticket=ST-xxx  │                   │
       │──────────────────>│                   │                   │
       │                   │                   │                   │
       │                   │ GET /serviceValidate?ticket=ST-xxx    │
       │                   │──────────────────>│                   │
       │                   │                   │                   │
       │                   │ XML: username, attrs                  │
       │                   │<──────────────────│                   │
       │                   │                   │                   │
       │                   │ SELECT/INSERT authuser                │
       │                   │──────────────────────────────────────>│
       │                   │                   │                   │
       │                   │ user record       │                   │
       │                   │<──────────────────────────────────────│
       │                   │                   │                   │
       │ 302 /protected (with session cookie)  │                   │
       │<──────────────────│                   │                   │
       │                   │                   │                   │
```

## Edge Cases

| Scenario | Behavior |
|----------|----------|
| User exists with RKAuth, logs in via CAS | Works - lookup by username finds existing user; session established |
| CAS server unavailable | Return error page with clear message; suggest retry |
| Invalid/expired CAS ticket | Return 401; redirect to login |
| User not in authuser (auto_create=False) | Return 403 Forbidden with message |
| CAS attributes missing email | Use `{username}@slac.stanford.edu` as default |
| CAS attributes missing displayname | Use username as displayname |

## Consequences

### Positive

1. **SSO Integration**: Users authenticate with existing SLAC credentials
2. **No Password Storage**: FASTDB never sees or stores passwords
3. **Automatic Onboarding**: First login creates account from CAS attributes
4. **Centralized Security**: Authentication delegated to institutional security team
5. **Backward Compatible**: RKAuth still works for service accounts and non-SLAC deployments

### Negative

1. **External Dependency**: CAS server availability required for login
2. **SLAC-Specific**: Default configuration assumes SLAC CAS
3. **Network Required**: No offline authentication possible

### Neutral

1. **BaseView Unchanged**: Session-based check works with both auth methods
2. **Database Schema Unchanged**: No migration required

## Alternatives Considered

### Alternative 1: Replace RKAuth Entirely

Remove RKAuth code and require CAS for all deployments.

**Rejected because:**
- Breaks non-SLAC deployments
- Removes fallback for service accounts
- No gradual migration path

### Alternative 2: OAuth2/OIDC Instead of CAS

Use modern OAuth2/OIDC protocol instead of CAS.

**Rejected because:**
- SLAC provides CAS, not OIDC
- CAS is simpler for server-side flow
- Existing documentation already describes CAS

### Alternative 3: Keep RKAuth as Primary

Make CAS optional, keep RKAuth as default.

**Rejected because:**
- Perpetuates password management burden
- Users prefer SSO
- Security team prefers centralized auth

## Dependencies

Add to `pyproject.toml` or Dockerfile:

```
python-cas>=1.6.0
```

The `python-cas` library provides CAS 3.0 protocol parsing without Flask dependencies.

## Testing Strategy

### Unit Tests (Core)

| Test | Category | Verifies |
|------|----------|----------|
| `test_cas_xml_parsing_success` | Specification | Valid CAS response parsing |
| `test_cas_xml_parsing_auth_failure` | Specification | CAS rejection handling |
| `test_cas_xml_parsing_malformed` | Boundary | Malformed XML error handling |
| `test_cas_login_url_construction` | Specification | URL building with params |

### Integration Tests (Adapter)

| Test | Category | Verifies |
|------|----------|----------|
| `test_cas_callback_creates_session` | Specification | Full flow with mock CAS |
| `test_cas_callback_provisions_user` | Specification | Auto-create new user |
| `test_cas_callback_updates_user` | Specification | Update existing user |
| `test_cas_server_timeout` | Failure Mode | Network timeout handling |
| `test_cas_with_rkauth_fallback` | Specification | Both auth methods work |

## Deployment

### Environment Variables

```yaml
env:
  - name: FASTDB_AUTH_METHOD
    value: "cas"  # or "both" during migration
  - name: FASTDB_CAS_SERVER_URL
    value: "https://cas.slac.stanford.edu"
  - name: FASTDB_CAS_SERVICE_URL
    value: "https://fastdb.slac.stanford.edu/auth/cas/callback"
  - name: FASTDB_CAS_AUTO_CREATE_USERS
    value: "true"
```

### Rollout Strategy

1. **Phase 1**: Deploy with `auth_method="both"` to test alongside RKAuth
2. **Phase 2**: Validate CAS login works for all target users
3. **Phase 3**: Switch to `auth_method="cas"` as default
4. **Phase 4**: Keep RKAuth available for service accounts if needed

## References

- [CAS Protocol 3.0 Specification](https://apereo.github.io/cas/6.6.x/protocol/CAS-Protocol-Specification.html)
- `extern/rkwebutil/rkwebutil/rkauth_flask.py` - RKAuth implementation (pattern to follow)
- `src/webserver/baseview.py` - Session authentication check
- `docs/fastdbauthn.rst` - Existing CAS documentation
- `docs/authN-cas.mmd.svg` - CAS sequence diagram
