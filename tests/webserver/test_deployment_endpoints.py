"""HTTP regression checks for the running webapp's public deployment endpoints.

These cover normal Compose operation, not injected database failures or changes
to build metadata at startup. No Flask dependency is needed in the test image.
"""

import json
import pathlib
import re

import requests


URL = 'http://webap:8080'


def test_healthy_without_login():
    response = requests.get(URL + '/healthz', timeout=10)
    assert response.status_code == 200
    assert response.json() == {'status': 'ok', 'checks': {'postgres': 'ok', 'mongodb': 'ok'}}


def test_build_info_without_login():
    # Compose does not normally generate this file; Helm installers do. Both
    # containers mount the same install/ directory at /fastdb.
    record = pathlib.Path('/fastdb/build-info.json')
    response = requests.get(URL + '/api/build-info', timeout=10)
    if not record.exists():
        assert response.status_code == 503
        assert response.json() == {
            'status': 'unavailable', 'error': 'Build identity has not been recorded.',
        }
    else:
        info = json.loads(record.read_text())
        assert response.status_code == 200
        assert response.json() == {'name': 'fastdb', **info}
        assert re.fullmatch('[0-9a-f]{40}', info['commit'])
        assert isinstance(info['version'], str)
        assert isinstance(info['dirty'], bool)

    repeated = requests.get(URL + '/api/build-info', timeout=10)
    assert repeated.status_code == response.status_code
    assert repeated.json() == response.json()
