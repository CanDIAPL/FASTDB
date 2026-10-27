# FASTDB development webapp behind MMP

This configuration prepares the existing webapp and existing-user login for:

```text
https://portal.candiapl.ca/launch/fastdb-dev/
```

It does not activate a public route. Follow section 2B of MMP's
`guides/tool-owner-deployment.md` for portal registration and its other
requirements.

## Configuration

[values-arbutus.yaml](fastdb/values-arbutus.yaml) includes the webapp proxy settings:

```yaml
webap:
  basePath: /launch/fastdb-dev
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

MMP must strip `/launch/fastdb-dev` before forwarding requests and set
`X-Forwarded-Prefix: /launch/fastdb-dev` and `X-Forwarded-Proto: https`.
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

The MMP-required `/healthz` and `/api/build-info` endpoints remain separate
work, as does testing real login and queries through MMP.

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
