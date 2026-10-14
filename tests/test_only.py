"""--only chooses which of the clients and the Flask blueprints are generated. The models always are."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from openapi_codegen.__main__ import main
from openapi_codegen.generate import generate, parse_parts

ROOT = Path(__file__).resolve().parents[1]
DEVICE_SPEC = ROOT / "tests" / "data" / "device_api.json"

MODELS = {"__init__.py", "models.py"}
CLIENTS = {"clients/__init__.py", "clients/_base.py", "clients/api_config.py"}
BLUEPRINTS = {"blueprints/__init__.py", "blueprints/_base.py", "blueprints/api_config.py"}


def _files(directory: Path) -> set[str]:
    return {path.relative_to(directory).as_posix() for path in directory.rglob("*.py")}


def _generate(out: Path, *only: str) -> int:
    return main([str(DEVICE_SPEC), "--out", str(out), *only])


def test_by_default_all_three_are_generated(tmp_path):
    assert _generate(tmp_path / "gen") == 0
    assert _files(tmp_path / "gen") == MODELS | CLIENTS | BLUEPRINTS


@pytest.mark.parametrize(
    ("only", "expected"),
    [
        (["--only", "clients"], MODELS | CLIENTS),
        (["--only", "blueprints"], MODELS | BLUEPRINTS),
        (["--only", "clients,blueprints"], MODELS | CLIENTS | BLUEPRINTS),
        (["--only", "clients", "--only", "blueprints"], MODELS | CLIENTS | BLUEPRINTS),
        (["--only", " blueprints , clients "], MODELS | CLIENTS | BLUEPRINTS),
        (["--only", "models"], MODELS),
        (["--only", "models,clients"], MODELS | CLIENTS),
    ],
)
def test_only_picks_the_parts_and_the_models_are_always_there(tmp_path, only, expected):
    assert _generate(tmp_path / "gen", *only) == 0
    assert _files(tmp_path / "gen") == expected


def test_naming_every_part_is_the_same_as_the_default(tmp_path):
    assert _generate(tmp_path / "default") == 0
    assert _generate(tmp_path / "named", "--only", "blueprints,clients") == 0
    for relative in _files(tmp_path / "default"):
        assert (tmp_path / "default" / relative).read_text() == (tmp_path / "named" / relative).read_text()


@pytest.mark.parametrize("value", ["client", "flask", "", "clients,", "clients,,blueprints"])
def test_an_unknown_or_empty_part_is_a_usage_error_and_writes_nothing(tmp_path, capsys, value):
    with pytest.raises(SystemExit) as exit_:
        _generate(tmp_path / "gen", "--only", value)

    assert exit_.value.code == 2
    assert "unknown part" in capsys.readouterr().err
    assert not (tmp_path / "gen").exists()


def _import_with_missing(parent: Path, module: str, *missing: str) -> subprocess.CompletedProcess:
    """Imports `module` in a fresh interpreter where importing any of `missing` fails, as if not installed."""
    code = f"import sys\nfor name in {list(missing)!r}:\n    sys.modules[name] = None\nimport {module}"
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=parent,
        env={**os.environ, "PYTHONPATH": str(parent)},
        capture_output=True,
        text=True,
        check=False,
    )


def test_each_part_imports_without_the_libraries_only_the_others_need(tmp_path):
    """The point of the flag: a client doesn't need Flask, a server doesn't need requests."""
    both = tmp_path / "both" / "device_api"
    clients = tmp_path / "clients" / "device_api"
    blueprints = tmp_path / "blueprints" / "device_api"
    models = tmp_path / "models" / "device_api"
    assert _generate(both) == 0
    assert _generate(clients, "--only", "clients") == 0
    assert _generate(blueprints, "--only", "blueprints") == 0
    assert _generate(models, "--only", "models") == 0

    # The control: with everything generated, leaving Flask out does break the blueprints, so the checks below
    # are really proving something.
    assert _import_with_missing(both.parent, "device_api.blueprints", "flask").returncode != 0
    assert _import_with_missing(both.parent, "device_api.clients", "requests").returncode != 0

    assert _import_with_missing(clients.parent, "device_api.clients", "flask").returncode == 0
    assert _import_with_missing(clients.parent, "device_api.models", "flask").returncode == 0
    assert _import_with_missing(blueprints.parent, "device_api.blueprints", "requests").returncode == 0
    assert _import_with_missing(models.parent, "device_api.models", "flask", "requests").returncode == 0


def test_narrowing_removes_the_generated_files_of_the_part_that_is_left_out(tmp_path, capsys):
    out = tmp_path / "gen"
    assert _generate(out) == 0
    capsys.readouterr()

    assert _generate(out, "--only", "clients") == 0

    assert f"removed {out / 'blueprints' / 'api_config.py'}" in capsys.readouterr().out
    assert _files(out) == MODELS | CLIENTS
    assert not (out / "blueprints").exists()  # the directory it emptied goes too
    assert _generate(out, "--only", "clients", "--check") == 0


def test_narrowing_leaves_alone_a_file_in_that_directory_it_did_not_write(tmp_path):
    out = tmp_path / "gen"
    assert _generate(out) == 0
    (out / "blueprints" / "mine.py").write_text("# hand written\n")

    assert _generate(out, "--only", "clients") == 0

    assert (out / "blueprints" / "mine.py").read_text() == "# hand written\n"
    assert _files(out) == MODELS | CLIENTS | {"blueprints/mine.py"}


def test_check_with_a_narrower_set_reports_the_parts_left_over(tmp_path, capsys):
    out = tmp_path / "gen"
    assert _generate(out) == 0
    capsys.readouterr()

    assert _generate(out, "--only", "clients", "--check") == 1

    err = capsys.readouterr().err
    assert "blueprints/api_config.py" in err
    assert "clients" not in err  # the parts that match aren't reported
    assert _files(out) == MODELS | CLIENTS | BLUEPRINTS  # --check wrote and removed nothing


def test_widening_adds_the_part_back(tmp_path):
    out = tmp_path / "gen"
    assert _generate(out, "--only", "clients") == 0
    assert _generate(out) == 0
    assert _files(out) == MODELS | CLIENTS | BLUEPRINTS


def test_the_python_api_takes_the_same_selection():
    document = json.loads(DEVICE_SPEC.read_text())

    assert set(generate(document)) == MODELS | CLIENTS | BLUEPRINTS
    assert set(generate(document, only=["clients"])) == MODELS | CLIENTS
    assert set(generate(document, only=[])) == MODELS
    assert parse_parts(None) == {"clients", "blueprints"}
    assert parse_parts(["models"]) == frozenset()
    with pytest.raises(ValueError, match="unknown part 'servers'"):
        parse_parts(["servers"])


def test_a_narrower_set_is_still_independent_of_the_hash_seed(tmp_path):
    out = tmp_path / "gen"
    cli = [sys.executable, "-m", "openapi_codegen", str(DEVICE_SPEC), "--out", str(out), "--only", "clients"]

    written = subprocess.run(
        cli, cwd=ROOT, env={**os.environ, "PYTHONHASHSEED": "1"}, capture_output=True, text=True, check=False
    )
    assert written.returncode == 0, written.stderr
    for seed in (2, 3, 4):
        checked = subprocess.run(
            [*cli, "--check"],
            cwd=ROOT,
            env={**os.environ, "PYTHONHASHSEED": str(seed)},
            capture_output=True,
            text=True,
            check=False,
        )
        assert checked.returncode == 0, f"seed {seed}:\n{checked.stderr}"
