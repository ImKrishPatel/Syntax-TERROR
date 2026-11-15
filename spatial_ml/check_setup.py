"""Basic import and project-directory validation."""

import sys
from pathlib import Path
from typing import List

import spatial_ml

from . import config


def validate_setup() -> List[str]:
    """Return setup problems without requiring any external datasets."""
    errors = []
    package_file = getattr(spatial_ml, "__file__", None)
    if package_file is None or not Path(package_file).is_file():
        errors.append("spatial_ml did not resolve to an importable package.")

    for name, path in config.PROJECT_DIRECTORIES.items():
        if not path.is_absolute():
            errors.append(f"The configured {name} path is not absolute: {path}")
            continue
        try:
            path.relative_to(config.PROJECT_ROOT)
        except ValueError:
            errors.append(
                f"The configured {name} path is outside the project root: {path}"
            )
            continue
        if not path.is_dir():
            errors.append(f"The configured {name} directory is unavailable: {path}")
    return errors


def main() -> int:
    """Run the setup check and print a concise result."""
    errors = validate_setup()
    if errors:
        print("Spatial ML setup check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("Spatial ML setup check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
