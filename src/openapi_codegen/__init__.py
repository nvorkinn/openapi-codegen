"""Generate Pydantic models, `requests` clients and Flask blueprints from an OpenAPI 3.x JSON or YAML document.

    python -m openapi_codegen openapi.json --out generated/device_api

See README.md."""

from .generate import generate, stale_files, write_files
from .schema import SpecError

__all__ = ["SpecError", "generate", "stale_files", "write_files"]
