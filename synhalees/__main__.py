"""``python -m synhalees`` -> the unified CLI (see :mod:`synhalees.cli`)."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
