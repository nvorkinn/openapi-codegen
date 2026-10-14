import json
import os
import subprocess
import sys
import uuid

import pytest
import requests
from flask import Flask
from pydantic import ValidationError
from support import DEVICE_SPEC, RICH_SPEC, ROOT, FlaskSession

from openapi_codegen.__main__ import main


def _config(models, **overrides):
    values = {
        "device_id": "d1",
        "interval": 60,
        "screen": {"orientation": "horizontal", "width": 10, "height": 20},
        "weather": {"api_key": "k", "location": "L"},
        "notice_board": {"postcode": None},
        "tfl": {"app_key": "k", "stop_ids": ["a"]},
        "spotify": {"enabled": True},
        "glowmarkt": {"username": None, "password": None},
        "pairing_code": None,
        "setup_missing": [],
    }
    return models.ConfigResponse(**{**values, **overrides})


def test_models_required_and_nullable(device_api):
    from device_api import models

    config = _config(models)
    assert config.pairing_code is None
    assert config.screen.orientation == "horizontal"
    with pytest.raises(ValidationError):
        models.ConfigResponse.model_validate({**config.model_dump(), "pairing_code": ...})
    data = config.model_dump()
    del data["pairing_code"]
    with pytest.raises(ValidationError):
        models.ConfigResponse.model_validate(data)
    with pytest.raises(ValidationError):
        models.Screen(orientation="diagonal", width=1, height=1)
    assert models.Error(error="x").error == "x"


def test_client_and_blueprint_round_trip(device_api):
    from device_api import models
    from device_api.blueprints import Api, ApiHTTPError
    from device_api.clients import ApiConfigClient, ApiError

    seen = {}
    api = Api()

    @api.authenticator("deviceSecret")
    def device_secret(context):
        if context.credential != "secret":
            raise ApiHTTPError(401, message="Missing or unknown secret.")

    @api.api_config_get()
    def get_config(*, x_device_name=None):
        seen["name"] = x_device_name
        return _config(models)

    app = Flask(__name__)
    api.register(app)
    session = FlaskSession(app)

    client = ApiConfigClient("http://broker", session, auth_token="secret")
    config = client.get(x_device_name="kitchen")
    assert isinstance(config, models.ConfigResponse)
    assert config.device_id == "d1"
    assert seen == {"name": "kitchen"}
    assert session.calls == [("GET", "http://broker/api/config")]

    with pytest.raises(ApiError) as caught:
        ApiConfigClient("http://broker", session, auth_token="wrong").get()
    assert caught.value.status_code == 401
    assert caught.value.body == models.Error(error="Missing or unknown secret.")


def test_an_api_with_nothing_registered_is_refused(device_api):
    from device_api.blueprints import Api, RegistrationError

    with pytest.raises(RegistrationError) as caught:
        Api().register(Flask(__name__))
    assert caught.value.problems == [
        "no handler is registered for api_config_get (GET /api/config)",
        "no authenticator is registered for the security scheme 'deviceSecret' (used by api_config_get)",
    ]


def test_register_adds_the_routes_under_the_prefix(device_api):
    from device_api.blueprints import Api

    api = Api()
    api.authenticator("deviceSecret")(lambda context: None)
    api.api_config_get()(lambda *, x_device_name=None: None)

    app = Flask(__name__)
    api.register(app, url_prefix="/v1")
    assert [rule.rule for rule in app.url_map.iter_rules() if rule.endpoint != "static"] == ["/v1/api/config"]
    assert [rule.endpoint for rule in app.url_map.iter_rules() if rule.endpoint != "static"] == ["api.api_config_get"]


def test_client_without_session_uses_requests_request(device_api, monkeypatch):
    from device_api import models
    from device_api.clients import ApiConfigClient

    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs["headers"]))
        response = requests.Response()
        response.status_code = 200
        response._content = _config(models).model_dump_json().encode()
        return response

    monkeypatch.setattr(requests, "request", fake_request)
    ApiConfigClient("http://broker/", auth_token="t").get(x_device_name="n")
    assert calls == [("GET", "http://broker/api/config", {"Authorization": "Bearer t", "X-Device-Name": "n"})]


def test_rich_models(pets_api):
    from pets_api import models

    pet = models.NewPet(name="Tom", kind="cat")
    assert pet.weight == 1.5
    assert pet.birth_date is None
    assert pet.parent is None
    with pytest.raises(ValidationError):
        models.NewPet(name="", kind="cat")
    with pytest.raises(ValidationError):
        models.NewPet(name="x", kind="fish")
    nick = models.NewPet.model_validate({"name": "a", "kind": "dog", "nick-name": "z"})
    assert nick.model_dump(by_alias=True, exclude_unset=True)["nick-name"] == "z"
    full = models.Pet(id=1, owner=None, name="a", kind="dog", token=uuid.uuid4())
    assert isinstance(full, models.NewPet)
    assert (
        models.Pet.model_validate(
            {
                "id": 1,
                "owner": "me",
                "name": "a",
                "kind": "cat",
                "parent": {"id": 2, "owner": None, "name": "b", "kind": "dog"},
            }
        ).parent.id
        == 2
    )


def test_rich_signatures(pets_api):
    import inspect

    from pets_api.clients import pets, pets_by_pet_id

    assert (
        str(inspect.signature(pets.PetsClient.get))
        == "(self, *, limit: 'int', tag: 'list[str] | None' = None) -> 'list[Pet]'"
    )
    assert list(inspect.signature(pets.PetsClient.post).parameters) == ["self", "body"]
    assert list(inspect.signature(pets_by_pet_id.PetsByPetIdClient.get).parameters) == ["self", "pet_id"]
    assert list(inspect.signature(pets_by_pet_id.PetsByPetIdClient.put).parameters) == [
        "self",
        "pet_id",
        "*",
        "if_match",
        "body",
    ] or list(inspect.signature(pets_by_pet_id.PetsByPetIdClient.put).parameters) == [
        "self",
        "pet_id",
        "if_match",
        "body",
    ]


def test_rich_round_trip(pets_api):
    from pets_api import models
    from pets_api.blueprints import Api, ApiHTTPError
    from pets_api.clients import ApiError
    from pets_api.clients.pets import PetsClient
    from pets_api.clients.pets_by_pet_id import PetsByPetIdClient

    store = {}
    api = Api()

    @api.pets_get()
    def list_pets(*, limit, tag=None):
        return [pet for pet in store.values() if tag is None or pet.name in tag][:limit]

    @api.pets_post()
    def create_pet(*, body):
        pet = models.Pet(id=len(store) + 1, owner=None, **body.model_dump())
        store[pet.id] = pet
        return pet, 201

    @api.pets_by_pet_id_get()
    def get_pet(*, pet_id):
        if pet_id not in store:
            raise ApiHTTPError(404, message="no such pet")
        return store[pet_id]

    @api.pets_by_pet_id_put()
    def put_pet(*, pet_id, if_match, body=None):
        assert if_match == "v1"
        store[pet_id] = store[pet_id].model_copy(update={"name": body.name})
        return store[pet_id]

    @api.pets_by_pet_id_delete()
    def delete_pet(*, pet_id):
        del store[pet_id]

    app = Flask(__name__)
    api.register(app)
    session = FlaskSession(app)
    pets = PetsClient("http://x", session)
    by_id = PetsByPetIdClient("http://x", session)

    created = pets.post(models.NewPet(name="Tom", kind="cat"))
    assert created.id == 1
    assert [p.name for p in pets.get(limit=5)] == ["Tom"]
    assert pets.get(limit=5, tag=["nobody"]) == []
    assert by_id.get(1).name == "Tom"
    assert by_id.put(1, if_match="v1", body=models.NewPet(name="Tim", kind="cat")).name == "Tim"
    assert by_id.delete(1) is None
    with pytest.raises(ApiError) as caught:
        by_id.get(1)
    assert caught.value.status_code == 404

    bad = app.test_client().post("/pets", json={"name": "", "kind": "cat"})
    assert bad.status_code == 400
    assert app.test_client().get("/pets").status_code == 400


def test_check_detects_stale_output(tmp_path):
    out = tmp_path / "gen"
    spec = DEVICE_SPEC
    assert main([str(spec), "--out", str(out)]) == 0
    assert main([str(spec), "--out", str(out), "--check"]) == 0
    (out / "models.py").write_text("# edited\n")
    assert main([str(spec), "--out", str(out), "--check"]) == 1
    assert (out / "models.py").read_text() == "# edited\n"
    assert main([str(spec), "--out", str(out)]) == 0
    assert main([str(spec), "--out", str(out), "--check"]) == 0


def _cli(*args, seed):
    """The command line in a fresh process, as CI runs it, with Python's string hashing seeded as given."""
    return subprocess.run(
        [sys.executable, "-m", "openapi_codegen", *map(str, args)],
        cwd=ROOT,
        env={**os.environ, "PYTHONHASHSEED": str(seed)},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("which", ["device", "rich"])
def test_output_does_not_depend_on_the_hash_seed(tmp_path, which):
    """Iterating a set is in a different order in every process, so anything generated from one would pass
    --check where it was written and fail it in CI. The in-process test above can't see that.
    """
    spec = DEVICE_SPEC
    if which == "rich":
        spec = tmp_path / "rich.json"
        spec.write_text(json.dumps(RICH_SPEC))
    out = tmp_path / "gen"

    written = _cli(spec, "--out", out, seed=1)
    assert written.returncode == 0, written.stderr
    for seed in (2, 3, 4, 5, 6):
        checked = _cli(spec, "--out", out, "--check", seed=seed)
        assert checked.returncode == 0, f"seed {seed} disagrees with seed 1:\n{checked.stderr}"
