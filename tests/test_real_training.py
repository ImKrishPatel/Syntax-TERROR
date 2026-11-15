import numpy as np
import pandas as pd

from spatial_ml.data.samples import TrainingSample
from spatial_ml.evaluation.train_real import train_real_models
from spatial_ml.pipeline.real_data import _write_sample_shard


def _sample(index: int, year: int) -> TrainingSample:
    target = np.zeros((3, 3), dtype=np.float32)
    target.flat[index % target.size] = 1.0
    latitudes = (-60.0, -40.0, -20.0, 20.0, 40.0, 60.0)
    longitudes = (-120.0, -70.0, -20.0, 20.0, 70.0, 120.0)
    return TrainingSample(
        modis_id=f"m{year}-{index}",
        modis_features={"frp": float(index + 1), "confidence": "80"},
        environmental_features=np.empty((3, 3, 0), dtype=np.float32),
        environmental_feature_names=(),
        target_grid=target,
        target_observations=pd.DataFrame(),
        metadata={
            "modis_id": f"m{year}-{index}",
            "observation_datetime": f"{year}-06-{index + 1:02d}T12:00:00",
            "modis_latitude": latitudes[index - 1],
            "modis_longitude": longitudes[index - 1],
            "target_data_status": "observed",
            "target_representation": "binary_viirs_point_observation_occupancy",
            "grid": {
                "height": 3,
                "width": 3,
                "resolution_m": 375.0,
            },
        },
    )


def test_real_training_runner_trains_saves_and_evaluates_both_models(
    tmp_path,
) -> None:
    shards = tmp_path / "samples"
    for year in range(2016, 2022):
        samples = [_sample(index, year) for index in range(1, 7)]
        _write_sample_shard(
            shards / f"{year}-06" / "training_samples.npz",
            samples,
        )

    manifest = train_real_models(
        shards,
        results_dir=tmp_path / "results",
        models_dir=tmp_path / "models",
        per_year_cap=6,
        random_seed=11,
        cnn_epochs=2,
        cnn_batch_size=4,
        cnn_ensemble_size=2,
        device="cpu",
    )

    assert manifest["status"] == "complete"
    assert set(manifest["strategies"]) == {"temporal", "spatial", "combined"}
    for strategy, result in manifest["strategies"].items():
        assert result["status"] == "complete", strategy
        assert all(
            result["partitions"][partition]["parent_count"] > 0
            for partition in ("train", "validation", "test")
        )
        assert result["model_artifacts"]["random_forest"]["training_parent_count"] > 0
        assert result["model_artifacts"]["spatial_ensemble"]["training_parent_count"] > 0
    temporal = manifest["strategies"]["temporal"]
    assert temporal["partitions"]["train"]["parent_count"] == 18
    assert temporal["partitions"]["validation"]["parent_count"] == 6
    assert temporal["partitions"]["test"]["parent_count"] == 12
    assert (tmp_path / "models" / "temporal" / "random_forest.joblib").is_file()
    assert (tmp_path / "models" / "temporal" / "spatial_ensemble.pt").is_file()
    assert (tmp_path / "results" / "temporal" / "metrics.json").is_file()
