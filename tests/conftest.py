import json

import pytest
from support import AUTH_SPEC, DEVICE_SPEC, RICH_SPEC, generate_package


@pytest.fixture
def device_api(tmp_path):
    yield from generate_package(tmp_path, json.loads(DEVICE_SPEC.read_text()), "device_api")


@pytest.fixture
def pets_api(tmp_path):
    yield from generate_package(tmp_path, RICH_SPEC, "pets_api")


@pytest.fixture
def auth_api(tmp_path):
    yield from generate_package(tmp_path, json.loads(AUTH_SPEC.read_text()), "auth_api")
