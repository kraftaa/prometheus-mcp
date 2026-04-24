"""prometheus-mcp package."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import tomllib

__all__ = ["__version__"]


def _detect_version() -> str:
    try:
        return version("prometheus-mcp")
    except PackageNotFoundError:
        try:
            pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
            with pyproject.open("rb") as fh:
                return tomllib.load(fh)["project"]["version"]
        except Exception:
            return "0.0.0"


__version__ = _detect_version()
