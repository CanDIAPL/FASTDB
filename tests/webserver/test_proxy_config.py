"""HTTP checks for the default, unproxied Compose webapp.

Prefixed HTTPS deployments need separate integration testing with a configured
proxy; these tests deliberately do not import Flask into the shell container.
"""

import requests

URL = 'http://webap:8080'

def test_root_ignores_untrusted_proxy_headers():
    response = requests.get(URL + '/', timeout=10, headers={
        'X-Forwarded-Prefix': '/ignored', 'X-Forwarded-Proto': 'https',
    })
    assert response.status_code == 200
    assert 'src="/static/fastdb.js"' in response.text
    assert '/ignored/' not in response.text


def test_static_assets():
    for filename in ('fastdb.js', 'fastdb_start.js', 'fastdb.css'):
        response = requests.get(URL + '/static/' + filename, timeout=10)
        assert response.status_code == 200


def test_session_cookie(test_user):
    # Challenge creation writes a real server-side session without completing
    # login. Use our own session so other tests' authenticated clients are intact.
    with requests.Session() as session:
        response = session.post(URL + '/auth/getchallenge',
                                json={'username': test_user.username}, timeout=10)
        assert response.status_code == 200
        cookie = response.headers['Set-Cookie']
        for attribute in ('Path=/', 'HttpOnly', 'SameSite=Lax'):
            assert attribute in cookie
        assert '; Secure' not in cookie

        response = session.post(URL + '/auth/logout', timeout=10)
        assert response.status_code == 200
        assert response.json() == {'status': 'Logged out'}
        cookie = response.headers['Set-Cookie']
        # RKAuth clears login details but retains the session cookie.
        for attribute in ('Path=/', 'HttpOnly', 'SameSite=Lax'):
            assert attribute in cookie
        assert '; Secure' not in cookie
