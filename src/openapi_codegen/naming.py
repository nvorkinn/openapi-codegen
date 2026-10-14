"""Turning names found in a spec into valid, unique Python identifiers."""

import keyword
import re

# Attributes of pydantic.BaseModel that a field must not shadow.
BASE_MODEL_ATTRIBUTES = frozenset(
    {
        "construct",
        "copy",
        "dict",
        "from_orm",
        "json",
        "parse_file",
        "parse_obj",
        "parse_raw",
        "schema",
        "schema_json",
        "update_forward_refs",
        "validate",
    }
)

# Names the generated modules import, which a generated class must not take.
RESERVED_CLASS_NAMES = frozenset(
    {"Annotated", "Any", "Api", "ApiError", "ApiHTTPError", "AuthContext", "Authenticator", "BaseClient"}
    | {"BaseModel", "Body", "Callable", "ClassVar", "ConfigDict", "Decorator", "Field", "Literal", "Operation"}
    | {"Param", "Protocol", "RegistrationError", "Registry", "Reply", "SchemeUse", "Sequence", "UUID"}
)

# What the generated `Api` class already has, which an operation's method must not replace.
RESERVED_METHOD_NAMES = frozenset(
    {"OPERATIONS", "authenticator", "blueprint", "error_body", "error_response", "read_body", "read_params"}
    | {"register", "respond", "verify"}
)


def is_identifier(name: str) -> bool:
    return name.isidentifier() and not keyword.iskeyword(name)


def snake(text: str) -> str:
    """`X-Device-Name` -> `x_device_name`, `pageSize` -> `page_size`, `HTTPHeader` -> `http_header`."""
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    return re.sub(r"[^0-9A-Za-z]+", "_", text).strip("_").lower()


def pascal(text: str) -> str:
    """`config_response` -> `ConfigResponse`; an already-PascalCase name is left alone."""
    parts = [part for part in re.split(r"[^0-9A-Za-z]+", text) if part]
    return "".join(part[:1].upper() + part[1:] for part in parts)


def parameter_name(wire: str, taken: set[str] | frozenset[str] = frozenset()) -> str:
    """A snake_case Python name for a parameter or header, avoiding keywords, digits first and `taken`."""
    name = snake(wire) or "param"
    if name[0].isdigit():
        name = f"p_{name}"
    if keyword.iskeyword(name) or name in BASE_MODEL_ATTRIBUTES:
        name += "_"
    while name in taken:
        name += "_"
    return name


def field_name(wire: str) -> tuple[str, bool]:
    """The model attribute for a JSON property, and whether it needs an alias back to `wire`.

    A property that is already a usable snake_case identifier keeps its name, so the model mirrors the payload;
    any other (`birthDate`) gets a snake_case attribute and an alias.
    """
    usable = (
        is_identifier(wire)
        and snake(wire) == wire
        and not wire.startswith("_")
        and not wire.startswith("model_")
        and wire not in BASE_MODEL_ATTRIBUTES
    )
    if usable:
        return wire, False
    name = snake(wire).strip("_") or "field"
    if name[0].isdigit():
        name = f"f_{name}"
    if keyword.iskeyword(name) or name in BASE_MODEL_ATTRIBUTES or name.startswith("model_"):
        name += "_"
    return name, name != wire


def method_name(text: str) -> str:
    """A snake_case method name for an operationId, avoiding keywords, a leading digit and `Api`'s own names."""
    name = snake(text) or "operation"
    if name[0].isdigit():
        name = f"op_{name}"
    if keyword.iskeyword(name) or name in RESERVED_METHOD_NAMES:
        name += "_"
    return name


def class_name(text: str) -> str:
    name = pascal(text) or "Model"
    if name[0].isdigit():
        name = f"M{name}"
    if keyword.iskeyword(name) or name in RESERVED_CLASS_NAMES:
        name += "Model"
    return name


class Names:
    """Hands out unique names: asking for a taken one gets `Name2`, `Name3`, ..."""

    def __init__(self) -> None:
        self._taken: set[str] = set()

    def reserve(self, wanted: str) -> str:
        name, count = wanted, 1
        while name in self._taken:
            count += 1
            name = f"{wanted}{count}"
        self._taken.add(name)
        return name
