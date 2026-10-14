"""Writing `blueprints/<route>.py` and `blueprints/__init__.py`.

A route module holds, per operation, its contract as data (an `Operation`) and the type of the handler that serves
it. `blueprints/__init__.py` has the `Api` class: a `Registry` with one typed registration method per operation. All
the behaviour (authenticating, reading the request, building the response, checking the registrations) is in
`runtime/blueprint_base.py`, which is copied next to them as `_base.py`.
"""

from typing import Any

from .emit_models import HEADER, render_field, stdlib_import_lines, write_local_imports
from .operations import Operation, Param, Route
from .schema import FieldDef
from .writer import Arg, CodeWriter, Raw, argument, import_lines, inline, quote

MAX_ARGUMENTS = 5  # ruff's PLR0913 limit (`self` and the injected `**kwargs` aren't counted)


def render_blueprint(route: Route, model_names: set[str], source: str, models_module: str) -> str:
    deps: set[str] = set()
    symbols: set[str] = {"Protocol"}
    for operation in route.operations:
        deps |= operation.type_deps()
        symbols |= operation.type_imports(server=True) | operation.type_imports(server=False)
    has_params = any(op.params for op in route.operations)
    has_body = any(op.body for op in route.operations)
    has_security = any(op.security for op in route.operations)
    pydantic: set[str] = set()
    if has_params:
        pydantic |= {"BaseModel", "ConfigDict"}
    if "Field" in symbols or any(p.name != p.wire or p.description for op in route.operations for p in op.params):
        pydantic.add("Field")
    base_names = {"Operation", "Reply"}
    if has_params:
        base_names.add("Param")
    if has_body:
        base_names.add("Body")
    if has_security:
        base_names.add("SchemeUse")

    w = CodeWriter()
    w.line(HEADER.format(source=source))
    w.line(f'"""The operations on ``{route.path}``: their contract and the types of their handlers."""')
    w.blank()
    w.line("from __future__ import annotations")
    w.blank()
    for line in stdlib_import_lines(symbols):
        w.line(line)
    if pydantic:
        w.blank()
        for line in import_lines("pydantic", pydantic):
            w.line(line)
    write_local_imports(w, models_module, deps & model_names, base_names)

    for index, operation in enumerate(route.operations):
        if operation.params:
            w.blank(2)
            _render_params_model(w, operation)
        # Only a class or function gets two blank lines after the imports.
        w.blank(1 if index == 0 and not operation.params else 2)
        _render_operation(w, route, operation)
        w.blank(2)
        _render_handler_type(w, route, operation)
    return w.getvalue()


def _render_params_model(w: CodeWriter, operation: Operation) -> None:
    with w.block(f"class _{operation.class_prefix}Params(BaseModel):"):
        w.docstring(f"The parameters of ``{operation.method.upper()} {operation.path}``, as validated.")
        w.blank()
        w.line("model_config = ConfigDict(populate_by_name=True, protected_namespaces=())")
        w.blank()
        for param in operation.params:
            kind = param.type if param.required else param.type.with_none()
            render_field(w, FieldDef(param.name, param.wire, kind, param.required, None, param.description))


def _render_operation(w: CodeWriter, route: Route, operation: Operation) -> None:
    w.line(f"{operation.constant} = Operation(")
    with w.block():
        w.argument("id", operation.id)
        w.argument("method", operation.method.upper())
        w.argument("rule", route.flask_rule)
        w.argument("default_status", operation.default_status)
        w.argument("responses", {r.status: Raw(r.expr) for r in operation.responses})
        if operation.security:
            w.argument("security", tuple(tuple(Raw(use) for use in _scheme_uses(alt)) for alt in operation.security))
        if operation.params:
            w.argument("params", tuple(Raw(_param(p)) for p in operation.params))
            w.argument("params_model", Raw(f"_{operation.class_prefix}Params"))
        if operation.body:
            w.argument("body", Raw(f"Body({operation.body.type.expr}, required={operation.body.required})"))
    w.line(")")


def _scheme_uses(alternative: dict[str, dict[str, Any]]) -> list[str]:
    uses = []
    for name, info in alternative.items():
        arguments = [quote(name), quote(info.get("type", ""))]
        for keyword, key in (("scheme", "scheme"), ("location", "in"), ("key", "name")):
            if key in info:
                arguments.append(f"{keyword}={quote(info[key])}")
        if info.get("scopes"):
            arguments.append(f"scopes={inline(tuple(info['scopes']))}")
        uses.append(f"SchemeUse({', '.join(arguments)})")
    return uses


def _param(param: Param) -> str:
    arguments = [quote(param.name), quote(param.wire), quote(param.location)]
    if param.is_array:
        arguments.append("multiple=True")
    return f"Param({', '.join(arguments)})"


def _handler_arguments(operation: Operation) -> list[str | Arg]:
    body = operation.body
    required: list[str | Arg] = [argument(f"{p.name}: {p.plain.expr}", p.name) for p in operation.params if p.required]
    optional: list[str | Arg] = [
        argument(f"{p.name}: {p.plain.with_none().expr} = None", p.name) for p in operation.params if not p.required
    ]
    if body:
        entry = f"body: {body.plain.expr}" if body.required else f"body: {body.plain.with_none().expr} = None"
        (required if body.required else optional).append(entry)
    named = [*required, *optional]
    return ["self", *(["*", *named] if named else []), "**injected: object"]


def _render_handler_type(w: CodeWriter, route: Route, operation: Operation) -> None:
    returns = "object" if operation.return_expr == "Any" else operation.return_expr
    label = f"{operation.method.upper()} {route.path}"
    with w.block(f"class {operation.class_prefix}Handler(Protocol):"):
        w.docstring(f"The signature of the handler for ``{label}``.")
        w.blank()
        codes = ["PLR0913"] if len(operation.params) + (1 if operation.body else 0) > MAX_ARGUMENTS else []
        w.signature("__call__", _handler_arguments(operation), f"Reply[{returns}]", codes)
        with w.block():
            w.docstring(f"Handle ``{label}``.", *_handler_notes(operation))


def _handler_notes(operation: Operation) -> list[str]:
    notes = [text for text in (operation.summary, operation.description) if text]
    notes.append(
        f"Answers {operation.default_status} with what it returns; return `(result, status)` for another status, "
        "or raise `ApiHTTPError` to send an error. Values from authentication arrive as keyword arguments."
    )
    return notes


def render_blueprints_init(routes: list[Route], source: str) -> str:
    modules = sorted(route.module for route in routes)
    ordered = [(route, op) for route in sorted(routes, key=lambda r: r.module) for op in route.operations]

    w = CodeWriter()
    w.line(HEADER.format(source=source))
    w.line('"""Serving the API: register a handler for each operation on `Api`, then call `Api.register`."""')
    w.blank()
    w.line("from __future__ import annotations")
    w.blank()
    w.line("from typing import TYPE_CHECKING")
    w.blank()
    for line in import_lines(".", modules):
        w.line(line)
    for line in import_lines("._base", ["ApiHTTPError", "AuthContext", "RegistrationError", "Registry", "Reply"]):
        w.line(line)
    w.blank()
    w.line("if TYPE_CHECKING:")
    with w.block():
        w.line("from collections.abc import Callable, Sequence")
        w.blank()
        w.line("from ._base import Decorator")
        for route in sorted(routes, key=lambda r: r.module):
            handlers = [f"{op.class_prefix}Handler" for op in route.operations]
            for line in import_lines(f".{route.module}", handlers):
                w.line(line)
    w.blank()
    w.statement("__all__ = ", ["Api", "ApiHTTPError", "AuthContext", "RegistrationError", "Reply"])
    w.blank(2)
    with w.block("class Api(Registry):"):
        w.docstring(
            "The server side of the API.",
            "Register a handler for every operation with its method here (as a decorator) and an authenticator for "
            "every security scheme with `authenticator`; `register` then checks that nothing is missing.",
        )
        w.blank()
        w.statement("OPERATIONS = ", tuple(Raw(f"{route.module}.{op.constant}") for route, op in ordered))
        for route, operation in ordered:
            w.blank()
            _render_registration(w, route, operation)
    return w.getvalue()


def _render_registration(w: CodeWriter, route: Route, operation: Operation) -> None:
    handler = f"{operation.class_prefix}Handler"
    returns = f"Callable[[{handler}], {handler}]"
    w.signature(operation.id, ["self", "*", "decorators: Sequence[Decorator] = ()"], returns)
    with w.block():
        w.docstring(
            f"Register the handler for ``{operation.method.upper()} {route.path}``.",
            "Its `decorators` wrap it, the first outermost.",
        )
        w.line(f"return self._handles({route.module}.{operation.constant}, decorators)")
