"""Run overlap matching against clearly labeled synthetic offline fixtures."""

from pathlib import Path

from spatial_ml.data import clean_firms_records, load_modis, load_viirs
from spatial_ml.pipeline import generate_overlap_dataset, get_overlap_period


def main() -> None:
    """Print the observed overlap and synthetic pair/candidate counts."""
    fixture_dir = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
    modis = clean_firms_records(
        load_modis(fixture_dir / "overlap_modis_synthetic.csv")
    )
    viirs = clean_firms_records(
        load_viirs(fixture_dir / "overlap_viirs_synthetic.csv")
    )
    overlap = get_overlap_period(modis, viirs)
    pairs, candidates = generate_overlap_dataset(modis, viirs)

    print(
        "Observed time-range intersection: "
        f"{overlap.overlap_start} to {overlap.overlap_end}"
    )
    print(f"Synthetic MODIS records in overlap: {len(pairs)}")
    print(f"Valid synthetic candidate pairs: {len(candidates)}")
    print(f"Matched MODIS records: {int(pairs['match_valid'].sum())}")
    print(
        pairs[
            [
                "modis_id",
                "viirs_id",
                "viirs_candidate_count",
                "spatial_distance_m",
                "temporal_difference_minutes",
                "match_quality",
            ]
        ].to_string(index=False)
    )


if __name__ == "__main__":
    main()
