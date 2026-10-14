"""The generated code, runtime files included, passes `ruff check --select ALL` and `ruff format --check`.

ALL has rules that contradict each other or need a policy rather than code, so the config below turns off exactly
those: D203 and D213 (opposites of D211 and D212), COM812 and ISC001 (clash with the formatter) and CPY001
(wants a copyright notice). Pydantic models need their imports at run time, which TC rules are told about.
"""

import json
import subprocess
import sys

import pytest
from support import AUTH_SPEC, DEVICE_SPEC, RICH_SPEC

from openapi_codegen.__main__ import main

CONFIG = """
line-length = 120
target-version = "py314"
src = ["."]

[lint]
select = ["ALL"]
ignore = ["D203", "D213", "COM812", "ISC001", "CPY001"]

[lint.flake8-type-checking]
runtime-evaluated-base-classes = ["pydantic.BaseModel"]
"""


def _json(schema):
    return {"application/json": {"schema": schema}}


# Everything in a spec that has to be fitted to Python or to the linter: awkward names, many parameters, a boolean
# in a path, builtins as parameter names, and descriptions that aren't docstring-shaped.
TRICKY_SPEC = {
    "openapi": "3.1.0",
    "info": {"title": "Tricky", "version": "1"},
    "paths": {
        "/things/{enabled}/{count}": {
            "parameters": [
                {"name": "enabled", "in": "path", "required": True, "schema": {"type": "boolean"}},
                {"name": "count", "in": "path", "required": True, "schema": {"type": "integer"}},
            ],
            "get": {
                "operationId": "listThings",
                "summary": "lists things, in no particular order",
                "description": (
                    "This one has several sentences. It isn't imperative.\n\nA second paragraph with a \\ backslash."
                ),
                "parameters": [
                    {"name": name, "in": "query", "schema": {"type": "string"}, "description": f"The {name}."}
                    for name in ("id", "type", "filter", "format", "page", "sort")
                ]
                + [{"name": "limit", "in": "query", "required": True, "schema": {"type": "integer"}}],
                "responses": {"200": {"description": "OK", "content": _json({"$ref": "#/components/schemas/Thing"})}},
            },
            "put": {
                "requestBody": {"required": True, "content": _json({"$ref": "#/components/schemas/Thing"})},
                "responses": {"204": {"description": "Done"}},
            },
        },
        "/odd": {"get": {"summary": "Is this a question?", "responses": {"200": {"description": "OK"}}}},
    },
    "components": {
        "schemas": {
            "Thing": {
                "type": "object",
                "description": "this describes a thing without a capital or a full stop",
                "properties": {
                    "birthDate": {"type": "string", "format": "date"},
                    "HTTPStatus": {"type": "integer"},
                    "kind": {"enum": ["a", "b"], "type": "string"},
                    "value": {},
                },
                "required": ["birthDate"],
            },
            "Long": {
                "type": "object",
                "description": (
                    "A very long first sentence that goes on and on and on, past any sensible width for a summary "
                    "line, and keeps going. Then another."
                ),
                "properties": {"x": {"type": "string"}},
            },
        }
    },
}


def _samples(tmp_path):
    specs = {
        "device_api": json.loads(DEVICE_SPEC.read_text()),
        "auth_api": json.loads(AUTH_SPEC.read_text()),
        "pets_api": RICH_SPEC,
        "tricky_api": TRICKY_SPEC,
    }
    for name, spec in specs.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(spec))
    return list(specs)


def _ruff(tmp_path, *args):
    (tmp_path / "ruff.toml").write_text(CONFIG)
    return subprocess.run(
        [sys.executable, "-m", "ruff", *args, "--config", str(tmp_path / "ruff.toml")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("only", [None, "models", "clients", "blueprints"])
def test_generated_code_passes_ruff_all(tmp_path, only):
    names = _samples(tmp_path)
    for name in names:
        extra = ["--only", only] if only else []
        assert main([str(tmp_path / f"{name}.json"), "--out", str(tmp_path / name), "--package", name, *extra]) == 0

    checked = _ruff(tmp_path, "check", "--output-format", "concise", *names)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    formatted = _ruff(tmp_path, "format", "--check", *names)
    assert formatted.returncode == 0, formatted.stdout + formatted.stderr
