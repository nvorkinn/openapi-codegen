"""The generated server side: registering handlers and authenticators, checking them against the spec, and
serving requests (authenticating, injecting what the authenticator provides, applying decorators).
"""

import functools
import json
import logging

import pytest
from flask import Flask, Response
from support import AUTH_SPEC, FlaskSession

from openapi_codegen.generate import generate
from openapi_codegen.schema import SpecError

SECRETS = {"r-secret": ("d1", "renderer"), "d-secret": ("d1", "display")}


def build(auth_api, *, skip=(), decorators=None):
    """An `Api` with every handler and authenticator registered, except those named in `skip`."""
    from auth_api.blueprints import Api, ApiHTTPError

    api = Api()
    calls = []

    def device_secret(context):
        calls.append(("deviceSecret", context.operation, context.scopes))
        entry = SECRETS.get(context.credential)
        if entry is None:
            raise ApiHTTPError(401, message="unauthorized")
        device_id, role = entry
        if context.scopes and role not in context.scopes:
            raise ApiHTTPError(403, message="forbidden")
        return {"device_id": device_id}

    def user_header(context):
        calls.append(("userHeader", context.operation, context.scopes))
        return {"user": context.credential}

    handlers = {
        "get_config": lambda *, x_device_name=None, device_id: {
            "device_id": device_id,
            "interval": 60 if x_device_name is None else len(x_device_name),
        },
        "create_pairing_code": lambda *, device_id: {"code": f"code-{device_id}", "expires_in_seconds": 600},
        "get_label": lambda *, label_id, **_: {"text": f"label {label_id}"},
        "set_label": lambda *, label_id, body, **_: {"text": f"{label_id}: {body.text}"},
        "admin_summary": lambda *, device_id, user: {"text": f"{device_id} {user}"},
        "health": lambda **_: None,
    }
    if "deviceSecret" not in skip:
        api.authenticator("deviceSecret", provides=("device_id",))(device_secret)
    if "userHeader" not in skip:
        api.authenticator("userHeader", provides=("user",))(user_header)
    for name, handler in handlers.items():
        if name not in skip:
            getattr(api, name)(decorators=(decorators or {}).get(name, ()))(handler)
    return api, calls


def client_for(api):
    app = Flask(__name__)
    api.register(app)
    return app.test_client()


def bearer(secret):
    return {"Authorization": f"Bearer {secret}"}


def test_the_authenticator_injects_what_it_provides(auth_api):
    api, calls = build(auth_api)
    client = client_for(api)

    response = client.get("/api/config", headers={**bearer("r-secret"), "X-Device-Name": "kitchen"})

    assert response.status_code == 200
    assert response.get_json() == {"device_id": "d1", "interval": 7}
    assert calls == [("deviceSecret", "get_config", ("renderer",))]


def test_a_missing_credential_is_a_401_with_a_challenge(auth_api):
    client = client_for(build(auth_api)[0])

    response = client.get("/api/config")

    assert response.status_code == 401
    assert response.get_json() == {"error": "Authentication is required"}
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert client.get("/api/config", headers={"Authorization": "Basic abc"}).status_code == 401


def test_a_bad_credential_is_whatever_the_authenticator_raises(auth_api):
    client = client_for(build(auth_api)[0])

    response = client.get("/api/config", headers=bearer("nope"))

    assert (response.status_code, response.get_json()) == (401, {"error": "unauthorized"})


def test_scopes_are_the_roles_the_authenticator_checks(auth_api):
    client = client_for(build(auth_api)[0])

    assert client.get("/api/config", headers=bearer("d-secret")).status_code == 403
    assert client.get("/api/config", headers=bearer("r-secret")).status_code == 200


def test_alternatives_are_tried_in_turn(auth_api):
    api, calls = build(auth_api)
    client = client_for(api)

    for secret in ("r-secret", "d-secret"):
        response = client.post("/api/devices/pairing-code", headers=bearer(secret))
        assert response.status_code == 200
        assert response.get_json() == {"code": "code-d1", "expires_in_seconds": 600}

    assert [scopes for _, _, scopes in calls] == [("renderer",), ("renderer",), ("display",)]
    assert client.post("/api/devices/pairing-code", headers=bearer("nope")).status_code == 401


def test_a_403_from_any_alternative_beats_a_401(auth_api):
    api, _ = build(auth_api)
    from auth_api.blueprints import ApiHTTPError

    api._authenticators["deviceSecret"].clear()

    @api.authenticator("deviceSecret", provides=("device_id",))
    def picky(context):
        # The first alternative says "not you" (401); the second recognises the caller but refuses it (403).
        raise ApiHTTPError(403 if context.scopes == ("display",) else 401, message="no")

    assert client_for(api).post("/api/devices/pairing-code", headers=bearer("x")).status_code == 403


def test_every_scheme_of_an_alternative_has_to_pass(auth_api):
    api, _ = build(auth_api)
    client = client_for(api)

    assert client.get("/api/admin", headers=bearer("r-secret")).status_code == 401
    assert client.get("/api/admin", headers={"X-User": "ann"}).status_code == 401
    assert client.get("/api/admin", headers={**bearer("d-secret"), "X-User": "ann"}).status_code == 403

    response = client.get("/api/admin", headers={**bearer("r-secret"), "X-User": "ann"})

    assert response.status_code == 200
    assert response.get_json() == {"text": "d1 ann"}


def test_an_open_operation_asks_for_nothing(auth_api):
    api, calls = build(auth_api)

    response = client_for(api).get("/health")

    assert (response.status_code, response.data) == (204, b"")
    assert calls == []


def test_authentication_comes_before_validation(auth_api):
    client = client_for(build(auth_api)[0])
    bad_body = {"json": {"text": ""}}

    assert client.put("/api/labels/7", headers=bearer("nope"), **bad_body).status_code == 401
    assert client.put("/api/labels/7", headers=bearer("r-secret"), **bad_body).status_code == 400

    ok = client.put("/api/labels/7", headers=bearer("r-secret"), json={"text": "hi"})
    assert (ok.status_code, ok.get_json()) == (200, {"text": "7: hi"})


def test_an_authenticator_that_returns_other_names_than_it_declared_is_a_500(auth_api, caplog):
    api, _ = build(auth_api)
    api._authenticators["deviceSecret"][0] = type(api._authenticators["deviceSecret"][0])(
        lambda context: {"surprise": 1}, ("device_id",)
    )

    with caplog.at_level(logging.ERROR):
        response = client_for(api).get("/api/config", headers=bearer("r-secret"))

    assert response.status_code == 500
    assert "returned ['surprise'] but declared ['device_id']" in caplog.text


def tag(order, name):
    def decorate(handler):
        @functools.wraps(handler)
        def wrapped(**kwargs):
            order.append((name, sorted(kwargs)))
            return handler(**kwargs)

        return wrapped

    return decorate


def test_decorators_wrap_the_handler_the_first_one_outermost(auth_api):
    order = []
    api, _ = build(auth_api, decorators={"get_config": [tag(order, "outer"), tag(order, "inner")]})

    response = client_for(api).get("/api/config", headers=bearer("r-secret"))

    assert response.status_code == 200
    names = ["device_id", "x_device_name"]
    assert order == [("outer", names), ("inner", names)]


def test_a_decorator_can_answer_instead_of_the_handler(auth_api):
    def refuse(handler):
        return lambda **kwargs: Response(f"not for {kwargs['device_id']}", status=418)

    api, _ = build(auth_api, decorators={"get_config": [refuse]})

    response = client_for(api).get("/api/config", headers=bearer("r-secret"))

    assert (response.status_code, response.data) == (418, b"not for d1")


def test_the_generated_client_calls_the_registered_handlers(auth_api):
    from auth_api import models
    from auth_api.clients import ApiConfigClient, ApiError

    api, _ = build(auth_api)
    app = Flask(__name__)
    api.register(app)
    session = FlaskSession(app)

    config = ApiConfigClient("http://x", session, auth_token="r-secret").get(x_device_name="abc")
    assert config == models.Config(device_id="d1", interval=3)
    with pytest.raises(ApiError) as caught:
        ApiConfigClient("http://x", session, auth_token="d-secret").get()
    assert caught.value.status_code == 403


# -- what registering checks --------------------------------------------------------------------------


def problems(api):
    from auth_api.blueprints import RegistrationError

    with pytest.raises(RegistrationError) as caught:
        api.register(Flask(__name__))
    return caught.value.problems


def test_a_fully_registered_api_passes(auth_api):
    build(auth_api)[0].verify()


def test_everything_that_is_missing_is_reported_at_once(auth_api):
    from auth_api.blueprints import Api

    assert problems(Api()) == [
        "no handler is registered for admin_summary (GET /api/admin)",
        "no handler is registered for get_config (GET /api/config)",
        "no handler is registered for create_pairing_code (POST /api/devices/pairing-code)",
        "no handler is registered for get_label (GET /api/labels/<label_id>)",
        "no handler is registered for set_label (PUT /api/labels/<label_id>)",
        "no handler is registered for health (GET /health)",
        (
            "no authenticator is registered for the security scheme 'deviceSecret' "
            "(used by admin_summary, create_pairing_code, get_config, get_label, set_label)"
        ),
        "no authenticator is registered for the security scheme 'userHeader' (used by admin_summary)",
    ]


def test_one_missing_handler_is_named(auth_api):
    assert problems(build(auth_api, skip=("get_label",))[0]) == [
        "no handler is registered for get_label (GET /api/labels/<label_id>)"
    ]


def test_two_handlers_for_one_operation_are_refused(auth_api):
    api, _ = build(auth_api)
    api.health()(lambda **_: None)

    assert problems(api) == ["2 handlers are registered for health (GET /health)"]


def test_authenticators_have_to_match_the_schemes_in_use(auth_api):
    api, _ = build(auth_api)
    api.authenticator("deviceSecret")(lambda context: None)
    api.authenticator("nothing")(lambda context: None)

    assert problems(api) == [
        "2 authenticators are registered for the security scheme 'deviceSecret'",
        "an authenticator is registered for 'nothing', which no operation uses",
    ]


def test_a_handler_has_to_accept_every_parameter_and_provided_name(auth_api):
    api, _ = build(auth_api, skip=("get_config", "set_label"))
    api.get_config()(lambda *, device_id: None)  # no x_device_name
    api.set_label()(lambda *, label_id, body: None)  # no device_id, which the authenticator always provides

    assert problems(api) == [
        "get_config (GET /api/config): the handler doesn't accept 'x_device_name'",
        "set_label (PUT /api/labels/<label_id>): the handler doesn't accept 'device_id'",
    ]


def test_a_handler_may_swallow_the_rest_with_kwargs(auth_api):
    api, _ = build(auth_api, skip=("get_config",))
    api.get_config()(lambda **_: {"device_id": "d1", "interval": 1})

    api.verify()


def test_a_handler_cannot_require_what_nobody_provides(auth_api):
    api, _ = build(auth_api, skip=("health",))
    api.health()(lambda *, who: None)

    assert problems(api) == [
        "health (GET /health): the handler requires 'who', which the API and its authenticators don't always provide"
    ]


def test_a_provided_name_cannot_shadow_an_api_parameter(auth_api):
    api, _ = build(auth_api)
    api._authenticators["deviceSecret"].clear()
    api.authenticator("deviceSecret", provides=("label_id", "device_id"))(lambda context: None)

    found = problems(api)
    assert "get_label (GET /api/labels/<label_id>): provided name 'label_id' is also an API parameter" in found
    assert "set_label (PUT /api/labels/<label_id>): provided name 'label_id' is also an API parameter" in found


def test_registering_an_incomplete_api_adds_no_routes(auth_api):
    api, _ = build(auth_api, skip=("health",))
    app = Flask(__name__)

    with pytest.raises(Exception, match="no handler is registered for health"):
        api.register(app)

    assert [rule.rule for rule in app.url_map.iter_rules() if rule.endpoint != "static"] == []


# -- naming operations --------------------------------------------------------------------------------


def spec_with(**operation_ids):
    document = json.loads(AUTH_SPEC.read_text())
    for path, method, operation_id in operation_ids.values():
        document["paths"][path][method]["operationId"] = operation_id
    return document


def test_operation_ids_become_snake_case_method_names(auth_api):
    from auth_api.blueprints import Api

    assert {"get_config", "create_pairing_code", "admin_summary", "health"} <= set(dir(Api))
    assert [op.id for op in Api.OPERATIONS] == [
        "admin_summary",
        "get_config",
        "create_pairing_code",
        "get_label",
        "set_label",
        "health",
    ]


def test_an_operation_id_used_twice_is_an_error():
    document = spec_with(a=("/api/config", "get", "same"), b=("/health", "get", "same"))

    with pytest.raises(SpecError, match="used by more than one operation"):
        generate(document)


def test_two_operation_ids_that_become_one_name_are_an_error():
    document = spec_with(a=("/api/config", "get", "getConfig"), b=("/health", "get", "get_config"))

    with pytest.raises(SpecError, match="both become 'get_config'"):
        generate(document)


def test_an_operation_id_cannot_replace_a_registry_method():
    document = spec_with(a=("/health", "get", "register"))

    files = generate(document)

    assert "def register_(" in files["blueprints/__init__.py"]
