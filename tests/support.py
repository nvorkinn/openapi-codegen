"""What the tests share: sample specs, a requests session answered by a Flask app, and generating a package."""

import importlib
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

import requests

from openapi_codegen.__main__ import main

ROOT = Path(__file__).resolve().parents[1]
# A copy of auth-broker's openapi.json: a real, small spec with a secured route, a header and nullable fields.
DEVICE_SPEC = ROOT / "tests" / "data" / "device_api.json"
# Device auth with roles as scopes, alternatives, two schemes at once, an open route, a path parameter and a body.
AUTH_SPEC = ROOT / "tests" / "data" / "auth_api.json"

ERROR = {"$ref": "#/components/schemas/Error"}


def _json(schema):
    return {"application/json": {"schema": schema}}


RICH_SPEC = {
    "openapi": "3.1.0",
    "info": {"title": "Pets", "version": "1"},
    "paths": {
        "/pets": {
            "get": {
                "summary": "List pets.",
                "parameters": [
                    {"name": "tag", "in": "query", "schema": {"type": "array", "items": {"type": "string"}}},
                    {"name": "limit", "in": "query", "required": True, "schema": {"type": "integer"}},
                ],
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": _json({"type": "array", "items": {"$ref": "#/components/schemas/Pet"}}),
                    }
                },
            },
            "post": {
                "summary": "Create a pet.",
                "requestBody": {"required": True, "content": _json({"$ref": "#/components/schemas/NewPet"})},
                "responses": {
                    "201": {"description": "Created", "content": _json({"$ref": "#/components/schemas/Pet"})},
                    "422": {"description": "Bad", "content": _json(ERROR)},
                },
            },
        },
        "/pets/{petId}": {
            "parameters": [{"name": "petId", "in": "path", "required": True, "schema": {"type": "integer"}}],
            "get": {
                "responses": {
                    "200": {"description": "OK", "content": _json({"$ref": "#/components/schemas/Pet"})},
                    "404": {"description": "Missing", "content": _json(ERROR)},
                },
            },
            "put": {
                "parameters": [{"name": "If-Match", "in": "header", "required": True, "schema": {"type": "string"}}],
                "requestBody": {"content": _json({"$ref": "#/components/schemas/NewPet"})},
                "responses": {"200": {"description": "OK", "content": _json({"$ref": "#/components/schemas/Pet"})}},
            },
            "delete": {"responses": {"204": {"description": "Gone"}}},
        },
    },
    "components": {
        "schemas": {
            "Error": {
                "type": "object",
                "properties": {"error": {"type": "string"}},
                "required": ["error"],
            },
            "NewPet": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "kind": {"enum": ["cat", "dog"], "type": "string"},
                    "birthDate": {"type": "string", "format": "date"},
                    "nick-name": {"type": "string"},
                    "weight": {"type": "number", "default": 1.5},
                    "parent": {"$ref": "#/components/schemas/Pet"},
                },
                "required": ["name", "kind"],
            },
            "Pet": {
                "allOf": [
                    {"$ref": "#/components/schemas/NewPet"},
                    {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "token": {"type": "string", "format": "uuid"},
                            "owner": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                        },
                        "required": ["id", "owner"],
                    },
                ],
            },
        }
    },
}


class FlaskSession(requests.Session):
    """A requests session whose calls are answered by a Flask test client."""

    def __init__(self, app):
        super().__init__()
        self.client = app.test_client()
        self.calls = []

    def request(self, method, url, params=None, headers=None, json=None, cookies=None, timeout=None, **_):
        self.calls.append((method, url))
        response = self.client.open(urlsplit(url).path, method=method, query_string=params, headers=headers, json=json)
        result = requests.Response()
        result.status_code = response.status_code
        result._content = response.data
        result.headers.update(response.headers)
        return result


def generate_package(tmp_path, spec, name, *args):
    source = tmp_path / f"{name}.json"
    source.write_text(json.dumps(spec))
    out = tmp_path / name
    assert main([str(source), "--out", str(out), "--package", name, *args]) == 0
    sys.path.insert(0, str(tmp_path))
    try:
        yield importlib.import_module(name)
    finally:
        sys.path.remove(str(tmp_path))
        for module in [m for m in sys.modules if m == name or m.startswith(f"{name}.")]:
            del sys.modules[module]
