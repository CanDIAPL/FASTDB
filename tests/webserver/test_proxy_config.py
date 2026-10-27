"""Test the real server startup settings without importing database-dependent views."""

import ast
import os
import pathlib
import types
import unittest
from unittest.mock import patch

import flask
from werkzeug.middleware.proxy_fix import ProxyFix


ROOT = pathlib.Path(__file__).resolve().parents[2]
PREFIX = '/launch/fastdb-dev'


def make_app(prefix='', secure=False):
    app = flask.Flask(__name__, template_folder=str(ROOT / 'src/webserver/templates'),
                      static_folder=str(ROOT / 'src/webserver/static'), static_url_path='/static')
    # Execute only the application's configuration and proxy setup. The rest of
    # server.py imports views and opens credential files that need a live DB setup.
    tree = ast.parse((ROOT / 'src/webserver/server.py').read_text())
    statements = [node for node in tree.body
                  if (isinstance(node, ast.Expr)
                      and ast.unparse(node.value).startswith('app.config.from_mapping('))
                  or (isinstance(node, ast.If) and 'FASTDB_BASE_PATH' in ast.unparse(node.test))]
    assert len(statements) == 2
    namespace = dict(app=app, os=os, ProxyFix=ProxyFix,
                     _flask_session_secret_key='test-only', config=types.SimpleNamespace(sessionstore='/tmp'))
    with patch.dict(os.environ, {'FASTDB_BASE_PATH': prefix, 'FASTDB_COOKIE_SECURE': str(secure)}):
        exec(compile(ast.Module(body=statements, type_ignores=[]), 'server-config', 'exec'), namespace)

    @app.route('/')
    def index():
        return flask.render_template('fastdb_webap.html.in')

    @app.route('/session', methods=['POST'])
    def session_check():
        flask.session['test'] = True
        return {'prefix': flask.request.script_root}

    @app.route('/logout', methods=['POST'])
    def logout():
        flask.session.clear()
        return {'status': 'ok'}

    return app


class TestProxyConfig(unittest.TestCase):
    def test_root(self):
        client = make_app().test_client()
        response = client.get('/', headers={'X-Forwarded-Prefix': '/ignored'})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'src="/static/fastdb.js"', response.data)
        cookie = client.post('/session').headers['Set-Cookie']
        self.assertIn('Path=/', cookie)
        self.assertNotIn('; Secure', cookie)

    def test_mmp(self):
        client = make_app(PREFIX, True).test_client(use_cookies=False)
        headers = {'X-Forwarded-Prefix': PREFIX, 'X-Forwarded-Proto': 'https'}
        response = client.get('/', headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn(f'content="{PREFIX}/"'.encode(), response.data)
        self.assertIn(f'src="{PREFIX}/static/fastdb.js"'.encode(), response.data)
        with client.get('/static/fastdb.js', headers=headers) as response:
            self.assertEqual(response.status_code, 200)
        self.assertEqual(client.get('/').status_code, 200)  # Direct Kubernetes probe.
        response = client.post('/session', headers=headers)
        self.assertEqual(response.json['prefix'], PREFIX)
        cookie = response.headers['Set-Cookie']
        for attribute in (f'Path={PREFIX}', 'Secure', 'HttpOnly', 'SameSite=Lax'):
            self.assertIn(attribute, cookie)
        response = client.post('/logout', headers={**headers, 'Cookie': cookie.split(';')[0]})
        self.assertIn('Max-Age=0', response.headers['Set-Cookie'])
        self.assertIn(f'Path={PREFIX}', response.headers['Set-Cookie'])


if __name__ == '__main__':
    unittest.main()
