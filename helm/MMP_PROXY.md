# FASTDB development webapp behind MMP

This configuration prepares the existing webapp and existing-user login for:

```text
https://staging.portal.candiapl.ca/launch/fastdb-alerts/
```

Staging MMP will point to the development VM temporarily, then to the dedicated
staging VM. The future production route will use the same path on
`https://portal.candiapl.ca`, with a separate production upstream.

It does not activate a public route. Follow section 2B of MMP's
`guides/tool-owner-deployment.md` for portal registration and its other
requirements.

## Configuration

[values-arbutus.yaml](fastdb/values-arbutus.yaml) includes the webapp proxy settings:

```yaml
webap:
  basePath: /launch/fastdb-alerts
  cookieSecure: true
```

When ready to apply the changes on the VM, from the repository root run:

```bash
./helm/scripts/install-singlenode-fastdb.sh helm/fastdb/values-arbutus.yaml
```

It uses the same namespace and storage settings as the original file, so this
updates that installation rather than creating a separate database. No image
rebuild is needed: the installer installs the current checkout's Python and
web files into the host-mounted code directory.

MMP must strip `/launch/fastdb-alerts` before forwarding requests and set
`X-Forwarded-Prefix: /launch/fastdb-alerts` and `X-Forwarded-Proto: https`.
The existing MMP Caddy example provides that arrangement.

## What the small application changes do

- Flask's standard proxy middleware reads the prefix supplied by MMP.
- The main page uses it for static-file links and passes it to JavaScript.
- JavaScript uses it for API and login/logout requests.
- Session cookies are scoped to the prefix and restricted to HTTPS.
- Kubernetes probes continue to request `/` directly.

Proxy-header handling is enabled only when `basePath` is set. Restrict the
application port to the trusted proxy as required by the MMP guide: these
headers are trusted when enabled. Leave `basePath` empty and
`cookieSecure: false` for the ordinary root-path development deployment.

For this MMP configuration, use an existing account created with
`create-user-singlenode.sh`. Browser password-reset support is deferred.
HTTPS-only cookies also mean an HTTP SSH tunnel will not retain this deployment's
login session; for tunnel-based development, use a copy of the values file with
`basePath: ""` and `cookieSecure: false`.

## Check the required endpoints

After reinstalling, run on the FASTDB VM:

```bash
curl --fail --show-error http://FASTDB_PRIVATE_IP:30080/healthz
curl --fail --show-error http://FASTDB_PRIVATE_IP:30080/api/build-info
```

Replace the address if using another VM. Neither endpoint requires login.
`/healthz` returns HTTP 200 when PostgreSQL and MongoDB respond, or HTTP 503
with the unavailable dependency's name. It does not expose credentials or
connection errors. It does not verify ingestion or every database migration.

The installer writes `install/build-info.json` alongside the installed code.
Workers read it at startup. `/api/build-info` reports `name`, a `git-<short SHA>`
version, the full 40-character `commit`, and `dirty`. A dirty checkout is useful
for development but is not an approved immutable release; commit the changes
and reinstall before supplying release evidence to MMP. Missing or invalid
identity returns HTTP 503, rather than guessing from a running checkout.

These checks do not replace testing real login and queries through MMP, or
the guide's release, persistence, backup and rollback requirements.

## Local checks

With the image already cached, run:

```bash
docker run --rm --network none --platform linux/amd64 \
  -v "$PWD:/code:ro" -e PYTHONPATH=/code/src \
  --entrypoint python \
  images.canfar.net/candiapl/fastdb-webap:manual-20260918 \
  /code/tests/webserver/test_proxy_config.py

node tests/webserver/proxy_urls.mjs
```

These database-free checks cover stripped-prefix and root requests, static
links, cookies, direct probe paths, and JavaScript API/login/logout URLs.

Run `/code/tests/webserver/test_deployment_endpoints.py` with the same Docker
command to check endpoint responses, dependency failures and recorded build identity.
