import pandas as pd

from spatial_ml.data.target_grid import LocalTargetGrid
from spatial_ml.features.modis_context import FEATURE_NAMES, build_modis_context


def test_neighbour_channels_point_toward_the_fire_front():
    when = pd.Timestamp("2022-08-01 19:00")
    modis = pd.DataFrame({
        "latitude": [55.0, 55.0, 55.0],
        "longitude": [-117.0, -116.99, -117.03],   # one neighbour ~640 m east, one ~1.9 km west
        "datetime": [when] * 3,
        "satellite": ["Terra"] * 3,
        "frp": [10.0, 50.0, 5.0],
        "scan": [1.2] * 3,
        "track": [1.1] * 3,
    })
    pairs = pd.DataFrame({
        "modis_id": ["p"], "modis_latitude": [55.0], "modis_longitude": [-117.0],
        "modis_datetime": [when], "modis_satellite": ["Terra"],
    })
    grid = LocalTargetGrid(55.0, -117.0)
    features = build_modis_context(pairs, modis, {"p": grid})["p"]
    assert features.feature_names == FEATURE_NAMES
    frp = features.values[..., 1]
    assert frp[1, 2] > frp[1, 0]            # east cells are closer to the hot neighbour
    assert features.values[1, 1, 3] == 1.2   # parent scan size
    assert features.values[1, 1, 0] == 1     # only the eastern neighbour is within 1 km of the centre
