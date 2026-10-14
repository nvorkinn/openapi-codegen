"""Writing `clients/<route>.py`: a class per route with one method per HTTP verb."""

import textwrap
from typing import TYPE_CHECKING

from .emit_models import HEADER, stdlib_import_lines, write_local_imports
from .writer import Arg, CodeWriter, Raw, argument

if TYPE_CHECKING:
    from .operations import Operation, Route

MAX_ARGUMENTS = 5  # ruff's PLR0913 and PLR0917 limits (a method's `self` is not counted)


def render_client(route: Route, model_names: set[str], source: str, models_module: str) -> str:
    deps: set[str] = set()
    symbols: set[str] = set()
    for operation in route.operations:
        deps |= operation.type_deps()
        symbols |= operation.type_imports(server=False)

    w = CodeWriter()
    w.line(HEADER.format(source=source))
    w.line(f'"""Client for ``{route.path}``."""')
    w.blank()
    w.line("from __future__ import annotations")
    stdlib = stdlib_import_lines(symbols)
    if stdlib:
        w.blank()
        for line in stdlib:
            w.line(line)
    write_local_imports(w, models_module, deps & model_names, {"BaseClient"})

    for operation in route.operations:
        w.blank()
        constant = operation.method.upper()
        w.statement(f"_{constant}_RESPONSES = ", {r.status: Raw(r.expr) for r in operation.responses})
        if operation.security:
            w.blank()
            w.statement(f"_{constant}_SECURITY = ", operation.security)

    w.blank(2)
    with w.block(f"class {route.prefix}Client(BaseClient):"):
        w.docstring(f"Calls for ``{route.path}``.")
        for operation in route.operations:
            w.blank()
            _render_method(w, route, operation)
    return w.getvalue()


def _render_method(w: CodeWriter, route: Route, operation: Operation) -> None:
    path_params = [p for p in operation.params if p.location == "path"]
    others = [p for p in operation.params if p.location != "path"]
    body = operation.body

    arguments: list[str | Arg] = ["self"]
    arguments.extend(
        argument(f"{p.name}: {p.plain.expr}", p.name, positional_bool=p.plain.expr == "bool") for p in path_params
    )
    positional = len(path_params)
    if body and body.required:
        arguments.append(f"body: {body.plain.expr}")
        positional += 1
    keyword: list[str | Arg] = [argument(f"{p.name}: {p.plain.expr}", p.name) for p in others if p.required]
    keyword += [argument(f"{p.name}: {p.plain.with_none().expr} = None", p.name) for p in others if not p.required]
    if body and not body.required:
        keyword.append(f"body: {body.plain.with_none().expr} = None")
    if keyword:
        arguments += ["*", *keyword]

    codes = []
    if len(operation.params) + (1 if body else 0) > MAX_ARGUMENTS:
        codes.append("PLR0913")
    if positional > MAX_ARGUMENTS:
        codes.append("PLR0917")
    returns = "object" if operation.return_expr == "Any" else operation.return_expr
    w.signature(operation.method, arguments, returns, codes)

    with w.block():
        w.docstring(*_docstring_parts(route, operation))
        constant = operation.method.upper()
        w.line("return self._send(")
        with w.block():
            w.line(f'"{constant}",')
            w.line(f'"{route.client_path}",')
            for keyword_name, location in (
                ("path_params", "path"),
                ("query", "query"),
                ("headers", "header"),
                ("cookies", "cookie"),
            ):
                entries = [p for p in operation.params if p.location == location]
                if entries:
                    w.argument(keyword_name, {(p.name if location == "path" else p.wire): Raw(p.name) for p in entries})
            if body:
                w.line("body=body,")
            w.line(f"responses=_{constant}_RESPONSES,")
            if operation.security:
                w.line(f"security=_{constant}_SECURITY,")
        w.line(")")


def _docstring_parts(route: Route, operation: Operation) -> list[str | tuple[str, ...]]:
    parts: list[str | tuple[str, ...]] = [f"Call ``{operation.method.upper()} {route.path}``."]
    parts += [text for text in (operation.summary, operation.description) if text]
    if operation.deprecated:
        parts.append("Deprecated.")
    described = [(p.name, p.description or f"The ``{p.wire}`` {p.location} parameter.") for p in operation.params]
    if operation.body:
        described.append(("body", operation.body.description or "The request body."))
    if described:
        rows = ["Args:"]
        for name, text in described:
            rows += textwrap.wrap(
                f"{name}: {' '.join(text.split())}", width=88, initial_indent="    ", subsequent_indent="        "
            )
        parts.append(tuple(rows))
    errors = [str(r.status) for r in operation.responses if not r.is_success]
    raised = "for an error status" + (f" ({', '.join(errors)})." if errors else ".")
    parts.append(("Raises:", f"    ApiError: {raised}", ""))
    return parts
