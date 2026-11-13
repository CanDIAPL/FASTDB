"""Database-free regression checks for the public deployment endpoints."""

import ast
import json
import pathlib
import re
import tempfile
import types
import unittest
from unittest.mock import MagicMock

import flask


ROOT = pathlib.Path(__file__).resolve().parents[2]


class TestDeploymentEndpoints(unittest.TestCase):
    def setUp(self):
        self.app = flask.Flask(__name__)
        self.postgres = MagicMock()
        self.mongo = MagicMock()
        self.database = types.SimpleNamespace(dbhost='postgres', dbport=5432, dbname='fastdb',
                                               dbuser='postgres', dbpasswd='never-return-this',
                                               MGCon=MagicMock(return_value=self.mongo))
        tree = ast.parse((ROOT / 'src/webserver/server.py').read_text())
        definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                       and node.name in ('healthz', 'load_build_info', 'build_info')]
        self.namespace = dict(app=self.app, psycopg=types.SimpleNamespace(connect=self.postgres),
                              pymongo=types.SimpleNamespace(timeout=MagicMock()), db=self.database,
                              json=json, pathlib=pathlib, re=re, _build_info=None)
        exec(compile(ast.Module(body=definitions, type_ignores=[]), 'deployment-endpoints', 'exec'),
             self.namespace)
        self.client = self.app.test_client()

    def test_healthy_without_login(self):
        response = self.client.get('/healthz')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {'status': 'ok', 'checks': {'postgres': 'ok', 'mongodb': 'ok'}})
        self.assertEqual(self.postgres.call_args.kwargs['connect_timeout'], 3)
        self.database.MGCon.assert_called_once_with(readonly=True)
        self.mongo.__enter__.return_value.db.command.assert_called_once_with('ping')

    def test_failed_dependencies_do_not_expose_errors(self):
        for dependency in ('postgres', 'mongodb'):
            with self.subTest(dependency=dependency):
                self.postgres.side_effect = None
                self.database.MGCon.side_effect = None
                failing = self.postgres if dependency == 'postgres' else self.database.MGCon
                failing.side_effect = RuntimeError('secret connection details')
                response = self.client.get('/healthz')
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json['checks'][dependency], 'unavailable')
                self.assertNotIn('secret', response.get_data(as_text=True))

    def test_missing_identity(self):
        self.assertEqual(self.client.get('/api/build-info').status_code, 503)

    def test_recorded_identity_is_stable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            self.namespace['__file__'] = str(root / 'webserver/server.py')
            record = root / 'build-info.json'
            info = {'version': 'git-' + 'a' * 12, 'commit': 'a' * 40, 'dirty': False}
            record.write_text(json.dumps(info))
            self.namespace['_build_info'] = self.namespace['load_build_info']()
            record.write_text(json.dumps({**info, 'commit': 'b' * 40}))
            response = self.client.get('/api/build-info')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json, {'name': 'fastdb', **info})

    def test_invalid_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            self.namespace['__file__'] = str(root / 'webserver/server.py')
            self.assertIsNone(self.namespace['load_build_info']())
            for value in ('not JSON', json.dumps({'commit': 'short', 'version': 'dev', 'dirty': True})):
                (root / 'build-info.json').write_text(value)
                self.assertIsNone(self.namespace['load_build_info']())


if __name__ == '__main__':
    unittest.main()
