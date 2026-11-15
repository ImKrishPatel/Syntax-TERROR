import pandas as pd

from shriya_backend.frp_multi_date_matching import (
    match_observations,
)

def test_viirs_detection_is_matched_only_once_across_dates():
    """A VIIRS detection matched on one date cannot be matched again on the next."""
    modis = pd.DataFrame({
        "latitude": [53.5],
        "longitude": [-113.5],
        "frp": [120.0],
        "timestamp": pd.to_datetime([
            "2024-08-10 23:55",
        ]),
        "observation_id": ["MODIS_2024-08-10_0"],
    })

    viirs = pd.DataFrame({
        "latitude": [53.5001],
        "longitude": [-113.5001],
        "frp": [110.0],
        "timestamp": pd.to_datetime([
            "2024-08-11 00:10",
        ]),
        "observation_id": ["VIIRS_2024-08-11_0"],
    })

    used_viirs = set()

    # First processing attempt
    first_matches = match_observations(
        modis,
        viirs,
        "2024-08-10",
        used_viirs,
    )

    used_viirs.update(
        first_matches["viirs_observation_id"].tolist()
    )

    # Second attempt: same VIIRS observation must be rejected
    second_matches = match_observations(
        modis,
        viirs,
        "2024-08-11",
        used_viirs,
    )

    assert len(first_matches) == 1
    assert len(second_matches) == 0
