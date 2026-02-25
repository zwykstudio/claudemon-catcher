"""Single source of truth for the package version, read from pyproject.toml."""

import pathlib

_TOML = pathlib.Path(__file__).resolve().parent / "pyproject.toml"


def _read() -> str:
    try:
        for line in _TOML.read_text().splitlines():
            if line.startswith("version"):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return "0.0.0"


VERSION = _read()
