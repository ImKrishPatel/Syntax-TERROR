"""Copy yearly FIRMS archives out of downloaded bundle ZIPs into data/raw.

Teammates shared the yearly ``DL_FIRE_*.zip`` requests packed inside bigger
ZIP files. This copies each yearly archive, unchanged, into ``modis/`` or
``viirs/`` under the raw data folder. Files that are already there with the
right size are left alone, so it is safe to run more than once.
"""

import argparse
import shutil
import zipfile
from pathlib import Path
from typing import Optional, Sequence

from spatial_ml import config

_SENSOR_FOLDERS = {"M-C61": "modis", "SV-C2": "viirs"}


def unpack_bundles(bundle_dir: str | Path, raw_dir: str | Path) -> int:
    """Return how many yearly archives were copied."""
    source = Path(bundle_dir)
    if not source.is_dir():
        raise FileNotFoundError(f"Bundle folder does not exist: {source}")
    copied = 0
    for bundle in sorted(source.glob("*.zip")):
        with zipfile.ZipFile(bundle) as outer:
            for member in outer.infolist():
                name = Path(member.filename).name
                if not (name.startswith("DL_FIRE_") and name.endswith(".zip")):
                    continue
                folder = next(
                    (
                        folder
                        for tag, folder in _SENSOR_FOLDERS.items()
                        if f"_{tag}_" in name
                    ),
                    None,
                )
                if folder is None:
                    print(f"Skipping {name} (sensor not used by this pipeline)")
                    continue
                target = Path(raw_dir) / folder / name
                if target.is_file() and target.stat().st_size == member.file_size:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                partial = target.with_name(target.name + ".part")
                with outer.open(member) as stream, partial.open("wb") as output:
                    shutil.copyfileobj(stream, output, length=4 * 1024 * 1024)
                partial.replace(target)
                copied += 1
                print(f"Copied {name}")
    return copied


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bundles", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, default=config.RAW_DATA_DIR)
    arguments = parser.parse_args(argv)
    try:
        copied = unpack_bundles(arguments.bundles, arguments.raw_dir)
    except (FileNotFoundError, OSError, zipfile.BadZipFile) as exc:
        parser.error(str(exc))
    print(f"{copied} archive(s) copied into {arguments.raw_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
