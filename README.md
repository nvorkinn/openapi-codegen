# openapi-codegen

Turns an OpenAPI 3.x document, JSON or YAML, into a Python package with:

- **Pydantic models** for the schemas,
- **`requests` clients**, one per route, and
- **Flask blueprints as a registry**: register a handler per operation and an authenticator per security scheme; startup fails if any is missing.

The generator needs nothing beyond the standard library (PyYAML only to read YAML). The code it writes needs
`pydantic`, plus `requests` for the clients and `flask` for the blueprints.

## Install

Install a released wheel. Every release has its wheel, its sdist and a `SHA256SUMS` file attached (see
[Releases](https://github.com/nvorkinn/openapi-codegen/releases)):

```bash
pip install https://github.com/nvorkinn/openapi-codegen/releases/download/v<version>/openapi_codegen-<version>-py3-none-any.whl
```

To read YAML specs as well, add PyYAML (`pip install pyyaml`) or install the wheel with the extra:
`pip install "openapi-codegen[yaml] @ https://github.com/nvorkinn/openapi-codegen/releases/download/v<version>/openapi_codegen-<version>-py3-none-any.whl"`.

As a dependency of a uv project, point at the same file; uv records its hash in `uv.lock`, so a build fails if the
file ever changes:

```toml
[tool.uv.sources]
openapi-codegen = { url = "https://github.com/nvorkinn/openapi-codegen/releases/download/v<version>/openapi_codegen-<version>-py3-none-any.whl" }

[dependency-groups]
dev = ["openapi-codegen"]
```

Prefer this to a git URL or tag: a tag can be moved, and a release's files are never replaced.

## Use

```bash
openapi-codegen openapi.json --out some/package        # or openapi.yaml
openapi-codegen openapi.json --out some/package --check
```

`--check` writes nothing and exits 1 if the files under `--out` differ from what would be generated, so a CI
step can fail when the spec and the generated code drift. The output is deterministic: the same spec gives the
same bytes in every process.

By default all three parts are generated. `--only` leaves some out, so a package that only calls an API doesn't
carry Flask views it can't import, and a server doesn't carry clients:

```bash
openapi-codegen openapi.json --out some/package --only clients       # models + clients; no Flask needed
openapi-codegen openapi.json --out some/package --only blueprints    # models + Flask blueprints
openapi-codegen openapi.json --out some/package --only clients,blueprints   # the same as the default
openapi-codegen openapi.json --out some/package --only models        # the models alone
```

`--package some.package` names the package the output is imported as, so that the clients and blueprints import
the models by that name instead of `from ..models import ...`. Give it if your linter bans parent-relative imports
(ruff's TID252, part of `select = ["ALL"]`).

The Pydantic models are always generated, because the clients and the blueprints are built on them. Values can be
comma separated or repeated (`--only clients --only blueprints`). If you narrow an existing `--out`, the generated
files of the part you dropped are removed, along with the directory if that leaves it empty (a file you wrote
there yourself is never touched), and `--check` reports them as out of date until you do.

A file named `.yaml` or `.yml` is read as YAML, anything else as JSON. YAML is read the way JSON would mean it:
only `true`/`false` are booleans (so an enum of `on`, `off`, `yes`, `no` stays strings), dates stay strings, an
unquoted `200:` key is the status code `"200"`, and a repeated key is an error naming its line.

## What it writes

- `models.py`: Pydantic models. An optional field is `T | None = None`; a required field that may be `null`
  is `T | None` with no default.
- `clients/<route>.py`: one `<Route>Client` per route with a `get()`, `post()`, … per verb. Headers, query and
  path parameters and the body are arguments. `Client(base_url, session=None, auth_token=None)` calls through
  the `requests.Session` if given, else `requests.request`; an error status raises `ApiError`.
- `blueprints/`: the server side, a registry you fill with callbacks. `blueprints/<route>.py` holds the contract of
  each operation and the type of its handler; `blueprints/__init__.py` has `Api`, with one registration method per
  operation, named after its `operationId` in snake case (`getConfig` becomes `get_config`; without an
  `operationId` it is `<route module>_<verb>`).

```python
from some.package.blueprints import Api, ApiHTTPError

api = Api()


@api.authenticator("deviceSecret", provides=("device_id",))
def device_secret(context):
    # context.credential is the bearer token, context.scopes the roles the operation asks for
    device, role = look_up(context.credential)
    if device is None:
        raise ApiHTTPError(401, message="unauthorized")
    if context.scopes and role not in context.scopes:
        raise ApiHTTPError(403, message="forbidden")
    return {"device_id": device.id}  # handed to the handler as a keyword argument


@api.create_pairing_code()
def create_pairing_code(*, device_id):
    return {"code": issue_code(device_id), "expires_in_seconds": 600}


@api.spotify_now_playing(decorators=[require_spotify_enabled(default=None)])  # the first is outermost
def now_playing(*, device_id): ...


api.register(app)  # checks everything, then adds the routes
```

  - **Nothing can be forgotten.** `api.register(app)` (or `api.verify()`, for a test) raises one
    `RegistrationError` listing every problem: an operation without a handler or with two, a security scheme
    without an authenticator, an authenticator no operation uses, a handler that doesn't accept a parameter of the
    spec or a value an authenticator provides, or that requires something nobody provides. Do it at startup and a
    spec change that the server hasn't caught up with fails the build, not a request.
  - **Security comes from the spec.** `security` is read as OpenAPI defines it: a list of alternatives, any one of
    which suffices, each naming schemes that must all pass. For a scheme that isn't OAuth2 the listed scopes are
    roles; "renderer or display" is `[{deviceSecret: [renderer]}, {deviceSecret: [display]}]`. The registry pulls the
    credential out of the request (bearer or basic token, or an API key from a header, query or cookie), answers 401
    itself when it is missing, and calls your authenticator for the scheme, before reading the parameters or body.
    The authenticator raises `ApiHTTPError` to refuse (when every alternative refuses, a 403 is preferred over a
    401) and returns exactly the names it declared in `provides`, which reach the handler as keyword arguments. A
    handler that doesn't need them takes `**_`.
  - **Filters that aren't part of the contract** go in `decorators=[...]` at registration. They wrap the handler,
    see the injected values, and may answer in its place by returning a `Response`.
  - Handlers get plain Python arguments (and a Pydantic object as `body`) and return a model, dict, list,
    `(body, status)` or a Flask `Response`; the body is checked against the type the spec declares. Raise
    `ApiHTTPError(status, ...)` to answer with an error; override `Api.error_body` to match your spec's error object.

Generated files are overwritten on every run, so keep custom logic in subclasses. Files it wrote in an earlier
run that it no longer produces are removed; it only ever removes files that begin with its own
`# Generated by openapi_codegen` marker.

## Lint

The generated code, `_base.py` files included, passes `ruff check --select ALL` and `ruff format --check`
(tested on every sample spec in `tests/test_lint.py`) with this config, which only turns off rules that contradict
each other or need a policy rather than code (D203/D213, COM812/ISC001, CPY001) and tells the type-checking rules
that Pydantic models need their imports at run time:

```toml
[tool.ruff.lint]
select = ["ALL"]
ignore = ["D203", "D213", "COM812", "ISC001", "CPY001"]

[tool.ruff.lint.flake8-type-checking]
runtime-evaluated-base-classes = ["pydantic.BaseModel"]
```

What depends on the spec is handled where it arises, with a `# noqa` on that line only: a handler with more than five
parameters (PLR0913), a parameter named like a builtin (`id`, `type`: A002), a boolean path parameter (FBT001).
Docstrings start with a one-line summary; the first sentence of a spec description is used when it reads as one,
otherwise a generated line is used and the description follows it. Model attributes are snake case, with an alias
for any other JSON property name (`birthDate` becomes `birth_date`). The guarantee is for the ruff version pinned in
`pyproject.toml`; a newer ruff may add rules.

## Limits

Only local `$ref`s and JSON bodies are supported.

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pytest
```

`tests/data/device_api.json` is a copy of [auth-broker](https://github.com/nvorkinn/auth-broker)'s
`openapi.json`, used as a small real-world spec.

## Releasing

Push a tag `v<version>` from `main`, e.g. `git tag v0.2.0 && git push origin v0.2.0`. That is all: the package's version
is stamped from the tag when it is built, so there is no version to raise first. (`version` in `pyproject.toml` is only
a placeholder for source checkouts.)

The [Release workflow](.github/workflows/release.yml), which follows pi-dashboard's, then:

1. checks the tag points at a commit on `main`;
2. builds the wheel and the sdist at that version, installs the wheel into a clean environment and generates from it;
3. creates the GitHub release (with generated notes; a pre-release such as `v0.3.0rc1` is marked as one) with the
   wheel, the sdist and `SHA256SUMS` attached, or, if a release for the tag already exists, adds the files to it.

The release only appears once everything it ships has been built and tried. A file already on a release is never
replaced, so a hash recorded in someone's lockfile stays valid. A tag that isn't `v<version>`, or isn't a valid
version, stops the workflow before it builds anything.

To build a tag that was pushed before the workflow existed (such as `v0.1.0`), run it by hand: Actions → Release →
Run workflow, with the tag.

## License

[MIT](LICENSE).
