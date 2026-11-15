"""Run the whole FIRMS matching pipeline, several calendar years at a time.

Step 1 normalizes every archive once (M2). Step 2 starts one
``spatial_ml.pipeline.real_data --year`` process per calendar year and keeps a
few of them running side by side. Every year writes to its own folder and its
own log file, and a year that already finished is skipped, so the command can
simply be started again after an interruption.
"""

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import IO, List, Optional, Sequence, Tuple

from spatial_ml import config
from spatial_ml.pipeline.real_data import (
    _first_acquisition_date,
    _ordered_archives,
    _prepare_normalized_archives,
)


def _lock_run_folder(output_dir: Path) -> Optional[IO[bytes]]:
    """Make sure only one copy of this command works on a folder at a time.

    The operating system releases the lock by itself when the process ends,
    even after a crash or a closed window, so a stale lock never blocks a
    restart. Returns None when another copy already holds the lock.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    handle = (output_dir / ".run.lock").open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def _year_is_complete(year_dir: Path) -> bool:
    manifest_path = year_dir / "processing_manifest.json"
    if not manifest_path.is_file():
        return False
    with manifest_path.open(encoding="utf-8") as stream:
        return json.load(stream).get("status") == "complete"


def _run_year(
    year: int,
    archives: Sequence[Path],
    processed_dir: Path,
    output_dir: Path,
    chunksize: int,
) -> Tuple[int, int]:
    year_dir = output_dir / f"year_{year}"
    if _year_is_complete(year_dir):
        print(f"{year}: already finished, skipping", flush=True)
        return year, 0

    log_dir = output_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-W",
        "ignore",
        "-m",
        "spatial_ml.pipeline.real_data",
        "--archives",
        *[str(path) for path in archives],
        "--processed-dir",
        str(processed_dir),
        "--output-dir",
        str(year_dir),
        "--chunk-size",
        str(chunksize),
        "--year",
        str(year),
    ]
    print(f"{year}: started", flush=True)
    with (log_dir / f"year_{year}.log").open("a", encoding="utf-8") as log:
        completed = subprocess.run(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=str(config.PROJECT_ROOT),
            check=False,
        )
    state = "finished" if completed.returncode == 0 else "FAILED (see its log)"
    print(f"{year}: {state}", flush=True)
    return year, completed.returncode


def run_all_years(
    archive_paths: Sequence[str | Path],
    *,
    processed_dir: str | Path,
    output_dir: str | Path,
    workers: int = 2,
    chunksize: int = 100_000,
) -> List[int]:
    """Normalize the archives, then match every calendar year. Returns failed years."""
    if workers < 1:
        raise ValueError("workers must be at least 1.")
    processed = Path(processed_dir)
    destination = Path(output_dir)
    lock = _lock_run_folder(destination)
    if lock is None:
        raise RuntimeError(
            "Another copy of this command is already running on "
            f"{destination}. Let that one finish (or close it) first."
        )
    try:
        return _run_all_years_locked(
            archive_paths, processed, destination, workers, chunksize
        )
    finally:
        lock.close()


def _run_all_years_locked(
    archive_paths: Sequence[str | Path],
    processed: Path,
    destination: Path,
    workers: int,
    chunksize: int,
) -> List[int]:
    archives = _ordered_archives(archive_paths)
    used = [path for sensor in ("MODIS", "VIIRS") for path in archives[sensor]]
    print(
        f"Using {len(archives['MODIS'])} MODIS and "
        f"{len(archives['VIIRS'])} VIIRS archives.",
        flush=True,
    )

    print("Step 1 of 2: cleaning the archives (this is done only once)", flush=True)
    _prepare_normalized_archives(archives, processed, chunksize)

    first_year = _first_acquisition_date(archives["VIIRS"][0]).year
    last_year = max(_first_acquisition_date(path).year for path in used) + 1
    years = list(range(first_year, last_year + 1))
    print(
        f"Step 2 of 2: matching MODIS with VIIRS for {years[0]}-{years[-1]}, "
        f"{workers} year(s) at a time",
        flush=True,
    )
    destination.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(
            pool.map(
                lambda year: _run_year(year, used, processed, destination, chunksize),
                years,
            )
        )
    return [year for year, code in results if code != 0]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Clean local FIRMS archives and build the MODIS/VIIRS training "
            "samples for every calendar year. No data are downloaded."
        )
    )
    parser.add_argument(
        "--archives",
        nargs="+",
        type=Path,
        default=[config.RAW_DATA_DIR],
        help="FIRMS ZIP archives, or folders that contain them",
    )
    parser.add_argument(
        "--processed-dir",
        type=Path,
        default=config.PROCESSED_DATA_DIR / "firms_real_full",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=config.OVERLAP_DATA_DIR / "real_full"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=max(1, min(4, (os.cpu_count() or 2) // 2)),
        help="how many calendar years to process at the same time",
    )
    parser.add_argument("--chunk-size", type=int, default=100_000)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        failed = run_all_years(
            arguments.archives,
            processed_dir=arguments.processed_dir,
            output_dir=arguments.output_dir,
            workers=arguments.workers,
            chunksize=arguments.chunk_size,
        )
    except (FileNotFoundError, FileExistsError, OSError, ValueError, RuntimeError) as exc:
        _parser().error(str(exc))
    if failed:
        print(f"These years did not finish: {failed}. Run the command again.")
        return 1
    print(f"All years finished. Samples are in {arguments.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
