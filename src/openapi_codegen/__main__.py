"""Command line: `python -m openapi_codegen SPEC --out DIR [--only PARTS] [--package NAME] [--check]`."""

import argparse
import sys
from pathlib import Path

from .generate import OPTIONAL_PARTS, generate, load, parse_parts, stale_files, write_files
from .schema import SpecError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="openapi-codegen",
        description=(
            "Generate Pydantic models, requests clients and Flask blueprints from an OpenAPI 3.x JSON or YAML file."
        ),
    )
    parser.add_argument("spec", type=Path, help="the OpenAPI file to read: .json, or .yaml/.yml (needs PyYAML)")
    parser.add_argument("--out", type=Path, required=True, help="the package directory to write")
    parser.add_argument(
        "--only",
        action="append",
        metavar="PARTS",
        help=(
            f"generate only these of {', '.join(OPTIONAL_PARTS)}, comma separated or repeated "
            "(default: all). The Pydantic models are always generated; `--only models` is models alone"
        ),
    )
    parser.add_argument(
        "--package",
        metavar="NAME",
        help=(
            "the dotted name the output is imported as (e.g. my_api), to import the models absolutely rather than "
            "relatively, for linters that ban parent-relative imports (default: relative imports)"
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="write nothing; exit 1 if the files under --out differ from what would be generated",
    )
    args = parser.parse_args(argv)
    try:
        parts = parse_parts(args.only)
    except ValueError as error:
        parser.error(str(error))

    try:
        files = generate(load(args.spec), source=args.spec.name, only=parts, package=args.package)
    except (SpecError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if args.check:
        stale = stale_files(files, args.out)
        for relative in stale:
            print(f"out of date: {args.out / relative}", file=sys.stderr)
        return 1 if stale else 0

    removed = write_files(files, args.out)
    for relative in removed:
        print(f"removed {args.out / relative}")
    print(f"Wrote {len(files)} files to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
