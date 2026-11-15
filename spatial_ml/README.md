# MODIS-to-VIIRS Spatial ML

## Purpose

This module estimates fine-scale fire-detection probability from coarse MODIS
observations and environmental context, while reporting uncertainty. The
current module includes local FIRMS ingestion, overlap matching, target-grid
construction, environmental raster alignment, training-sample packaging, and
a probabilistic Random Forest baseline plus a small spatial convolutional
ensemble. No NASA data is bundled, and no historical product has been
generated.

## Scientific problem

MODIS and VIIRS observe active fires at different spatial resolutions. The
project estimates the probability of observed VIIRS point occupancy in local
target cells from MODIS observations and environmental context. This is
probabilistic spatial estimation/downscaling, not recovery of a true
historical 375 m VIIRS dataset or reconstruction of observations that were
never collected.

## MODIS and VIIRS spatial resolutions

- MODIS: approximately 1,000 m (1 km).
- VIIRS: approximately 375 m.

These are nominal resolutions; downstream processing will need to account for
the source products' geolocation, projection, and observation characteristics.

## Overlap-period training concept

The MODIS/VIIRS overlap begins in 2012. Paired observations from the overlap
period can provide training examples for estimating fine-scale point-detection
probabilities.
The overlap end year is intentionally not assumed: set
`SPATIAL_ML_OVERLAP_END_YEAR` when a dataset-specific end year is selected.
No real training examples or NASA observations are included in this repository.

## Environmental features

Environmental spatial context may include elevation, slope, aspect, and land
cover. The raster feature interface is described below; no environmental
provider or data source is selected.

## Probabilistic baseline model

The first baseline is a probabilistic Random Forest. It predicts, per target
cell, the probability of observed VIIRS point occupancy using MODIS FRP and
confidence, local cell position, and aligned environmental features. Missing
numeric inputs are imputed using medians fitted on the training data only.
Confidence labels (`low`, `nominal`, and `high`) are represented separately
from numeric confidence values.

The target remains the Milestone 4 binary point-occupancy label: a mapped VIIRS
point is positive; an unoccupied cell is not proof that no fire occurred.
Samples with no in-grid positive VIIRS evidence are excluded during fitting and
reported in a warning and `excluded_sample_ids`. This baseline does not
reconstruct physical sensor footprints or estimate a true fire state.

Per-cell uncertainty is the standard deviation of positive-class probabilities
across individual trees. It is an uncalibrated ensemble-spread proxy, not a
confidence interval or a guarantee of prediction coverage.

The API is available from `spatial_ml.models`:

```python
from spatial_ml.models import ProbabilisticRandomForest

model = ProbabilisticRandomForest(random_state=42).fit(training_samples)
estimate = model.predict(sample)
probability = estimate.probability
uncertainty = estimate.uncertainty_std
```

`estimate.metadata` labels the output as a model estimate and records the
target interpretation and uncertainty method. This repository does not train
the model on NASA data or generate historical outputs.

## Milestone 6 spatial probability model

`ProbabilisticSpatialEnsemble` is a second, spatial-context model for the same
observed VIIRS point-occupancy probability task as the Random Forest. It uses
configurable small 3 × 3 convolution blocks with padding to preserve
`[rows, columns]`, then a per-cell sigmoid output. Grid height and width default
to the existing M4 configuration; the default 3 × 3 grid is unchanged.
VIIRS is the observed reference label during the overlap period, not perfect
physical-fire ground truth.

The shared M5 feature builder supplies MODIS FRP, valid MODIS confidence,
east/west and north/south cell offsets, and available environmental channels.
The spatial tensor has shape `[channels, rows, columns]` per sample. Flattened
M5 rows are interpreted in row-major order: row 0 is north-to-south grid row 0
and columns proceed west-to-east. The channel list, including any missingness
indicators, is recorded with each prediction and model artifact. VIIRS targets,
confidence, FRP, target status, and evidence metadata are not input channels.

Training requires observed binary `observed_viirs_occupancy` targets and rejects
estimated targets. Parent MODIS samples are partitioned by
`split_samples_by_parent`; all cells belonging to one MODIS ID stay together.
The split helper is a reproducible random parent-level split, not a
time/event-aware validation strategy. Timestamps and event IDs remain attached
to samples for a later time/event-based study.

For each fit, feature medians, missingness indicators, means, and scales are
learned from training samples only. Those values are persisted and reused
unchanged during prediction. The model trains multiple independently
initialized small CNNs; the output is their ensemble-mean probability and the
per-cell standard deviation across members. This is an uncalibrated ensemble
disagreement uncertainty proxy, not a confidence interval or posterior.
Fixed seeds support repeatable CPU tests; bitwise reproducibility across
different PyTorch versions and hardware is not guaranteed.

The shared `spatial_ml.evaluation.evaluate_probability_grids` interface
provides Brier score, log loss, ROC-AUC and PR-AUC when both classes are
present, plus binned reliability data that also summarizes the probability
distribution. It evaluates probabilities but does not fit a calibration
transform. These utilities do not constitute a real-data comparison or
validation result.

The model artifact stores architecture settings, channel names, grid
dimensions, training-fitted preprocessing, ensemble weights/seeds, target
definition, and parent-sample provenance. Save/load uses a weights-only
PyTorch checkpoint. A GAN discriminator is intentionally omitted: a 3 × 3
binary point-occupancy task does not justify adversarial image synthesis, and
the desired output is a probability surface rather than generated VIIRS-like
imagery.

```python
from spatial_ml.data import split_samples_by_parent
from spatial_ml.evaluation import evaluate_probability_grids
from spatial_ml.models import ProbabilisticSpatialEnsemble

split = split_samples_by_parent(samples, random_seed=42)
model = ProbabilisticSpatialEnsemble(random_seed=42).fit(split.train)
estimate = model.predict(split.test[0])
scores = evaluate_probability_grids(
    split.test[0].target_grid, estimate.probability
)
model.save("models/spatial_ensemble.pt")
```

This example shows the interface only. No real NASA data was used to fit,
calibrate, compare, or validate either model.

## Milestone 7: held-out evaluation

Milestone 7 adds a first leakage-resistant evaluation workflow for the
Random Forest and spatial ensemble. Both models must be trained on the same
parent MODIS samples and are scored on the same eligible held-out parent
samples. Every local cell from a parent stays in its partition.

Supported split strategies are:

- `temporal`: inclusive training and validation end dates/years, with later
  samples held out for testing.
- `spatial`: deterministic equal-area EPSG:6933 spatial blocks assigned to
  train, validation, and test.
- `combined`: a strict time-window and spatial-block intersection. Samples
  outside matching partition intersections are explicitly reported as dropped.

Probability calibration supports Platt scaling and isotonic regression. A
calibrator is fit from validation predictions only, records its validation
parent IDs, and is applied once to the test probabilities. If validation
support is insufficient, calibrated results are marked unavailable rather
than tuned on the test data.

Reports include counts and prevalence, Brier score, log loss, ROC-AUC/PR-AUC
when defined, expected calibration error, reliability bins, parent-level Brier
summaries, uncertainty/error and risk-versus-coverage diagnostics, and
year/spatial-block/land-cover subgroup metrics when those groupings are
available. Raw test probabilities, available calibrated probabilities,
uncertainty proxies, and observed target grids are retained in a compressed
NPZ file. JSON, Markdown, reliability CSV, and validation-fitted calibration
artifacts accompany it. Input file hashes, split settings, feature names,
model settings, and software versions are recorded for reproducibility.

Run an evaluation using local files you provide:

```bash
python -m spatial_ml.evaluation.run_local \
  --modis /path/to/local_modis_firms.csv \
  --viirs /path/to/local_viirs_firms.csv \
  --train-end 2020 \
  --validation-end 2021 \
  --output-dir results/milestone_7
```

The CLI accepts optional `--elevation` and `--land-cover` local rasters.
Spatial-block evaluation additionally requires `--block-size-m`; temporal and
combined evaluation require `--train-end` and `--validation-end`. Use
`python -m spatial_ml.evaluation.run_local --help` for all options. The command
does not download data. File hashes establish input identity, not NASA
provenance or data quality.

Synthetic fixtures support software tests only and are not scientific evidence
or model-performance results. This checkout currently contains no real local
FIRMS observations, so no real-data model comparison, calibration, or
validation has been performed. The evaluation workflow does not produce
historical estimates.

## Historical 2000–2011 synthesis

MODIS provides the historical coarse-resolution observations for 2000–2011.
After development and evaluation with overlap-period observations, a validated
model may be applied to those historical MODIS observations to produce a
MODEL-ESTIMATED FINE-SCALE FIRE-DETECTION PROBABILITY + UNCERTAINTY product.
Historical application is outside Milestone 6; this milestone does not create
historical VIIRS observations or a historical VIIRS dataset.

## Outputs

The configured project folders are:

- `data/raw/`: source data, with `modis/` and `viirs/` subdirectories.
- `data/processed/`: prepared input data.
- `data/overlap/`: data associated with the MODIS/VIIRS overlap.
- `data/environmental/`: environmental inputs such as elevation or land cover.
- `models/`: saved model artifacts.
- `outputs/`: generated spatial products.
- `results/`: evaluation summaries and other results.

Data and generated artifacts are excluded from Git; `.gitkeep` files preserve
the empty directory structure.

## Observed versus estimated data

MODIS and VIIRS source observations must remain distinguishable from derived
products. Historical outputs must be labeled MODEL-ESTIMATED FINE-SCALE FIRE
PROBABILITY + UNCERTAINTY. They must not be described as a recovered 375 m
fire dataset, reconstructed VIIRS, or historical 375 m VIIRS observations.

## FIRMS ingestion and cleaning

The ingestion layer accepts local CSV files only; it does not download NASA
data.

- Expected MODIS input: a FIRMS CSV with latitude/longitude, acquisition date
  and time, and available satellite, instrument, confidence, FRP, and day/night
  columns.
- Expected VIIRS input: the equivalent local VIIRS FIRMS CSV. Common column
  variants such as `lat`/`lon`, `acq_date`/`acq_time`, and `day_night` are
  normalized.
- `load_modis(path)` and `load_viirs(path)` normalize schema and explicitly set
  the sensor label. Loading does not discard rows.
- The normalized schema includes `latitude`, `longitude`, `date`, `time`,
  `datetime`, `sensor`, `satellite`, `instrument`, `confidence`, `frp`, and
  `daynight`. Unrecognized source columns are retained.
- Date/time values are parsed into a date, an HHMM time string, and a datetime.
  Midnight and leading-zero acquisition times are supported. Missing or
  malformed timestamps remain missing during normalization and are excluded
  by cleaning with an informational log message.
- Cleaning removes rows with missing/out-of-range coordinates and rows with
  missing or malformed datetimes. Duplicate observations are identified by
  sensor, coordinates, datetime, satellite, and instrument; only later exact
  matches on those identifying fields are removed.
- Missing, non-numeric, non-finite, or negative FRP is represented as missing;
  those records are retained. The source confidence value is preserved; values
  outside the supported numeric range or known categorical labels are flagged
  in `confidence_valid`, not filtered. No confidence threshold is applied.
- `quality_control_summary(raw, cleaned)` returns counts, retained percentage,
  coordinate/date ranges, missing and invalid FRP/confidence counts, duplicate
  count, sensor, satellites, and day/night counts.
- `temporal_range_summary(modis, viirs)` reports observed earliest/latest
  datetimes and whether the supplied ranges overlap. The result is based on
  supplied records, not assumed mission date ranges.

`tests/fixtures/` contains tiny, clearly labeled synthetic CSVs for offline
tests; they are not NASA observations. Run the demonstration with:

```bash
python -m spatial_ml.data.check_sample_ingestion
```

The ingestion layer standardizes observed MODIS and VIIRS FIRMS records. It
does not perform spatial harmonization or generate synthetic VIIRS observations.

## MODIS–VIIRS overlap matching

The overlap dataset is built from normalized, cleaned observed MODIS and VIIRS
records. The period when both sensors provide observations supplies examples
for comparing their different spatial resolutions. The overlap period is
derived from the supplied datetime ranges; the pipeline does not assume
mission start/end years or claim that a particular calendar period is present.
`get_overlap_period(modis, viirs)` returns the inclusive datetime intersection
and raises a clear error if there is no intersection.

Matching defaults are configured in `spatial_ml/config.py`:

- Temporal tolerance: `DEFAULT_TIME_TOLERANCE_MINUTES` (30 minutes).
- Spatial radius: `SPATIAL_MATCH_RADIUS_M` (1,000 m, a starting heuristic).

Candidates must be from the same acquisition date and within the absolute
timestamp tolerance. This avoids treating separate days as equivalent while
allowing for small sensor acquisition-time differences. Matching evaluates
complete UTC calendar dates touched by the observed timestamp-range
intersection, so near-simultaneous records just outside either dataset's exact
first/last timestamp are not discarded before applying the tolerance. The
one-kilometre radius is an observation-to-observation proximity threshold near
the nominal MODIS footprint scale; it is not a physical MODIS pixel boundary
and has not been scientifically validated.

The spatial search uses a 3D unit-sphere `cKDTree` to efficiently find
geographic neighbors without assuming one projection is valid globally.
Candidates are then checked with WGS84 geodesic distances in meters. Relative
VIIRS positions are represented in a MODIS-centered local azimuthal-equidistant
coordinate frame: `delta_x_m` is positive east and `delta_y_m` is positive
north.

`generate_overlap_dataset(modis, viirs)` returns two tables:

- **Pair table:** one row per MODIS observation on common UTC calendar dates
  touched by the observed overlap interval, including unmatched MODIS
  observations. It preserves the requested fields from both sensors,
  selected-match distance/time difference, candidate count, relative
  coordinates, and `match_valid` plus the binary `match_quality` (`valid` or
  `invalid`).
- **Candidate table:** one row per MODIS/VIIRS candidate satisfying the
  same-day, time-tolerance, and spatial-radius rules. It preserves both
  observations' fields, IDs, `distance_m`, absolute
  `time_difference_minutes`, relative coordinates, and a `valid` flag. This
  retains multiple VIIRS positions for later target-grid construction.

Conceptually:

```text
MODIS observation
  -> find same-date VIIRS observations within the time tolerance
  -> keep those within the WGS84 spatial radius
  -> preserve all valid candidates and their VIIRS coordinates
  -> select the nearest candidate for the one-to-one pair table
  -> retain local east/north offsets for a later 375 m target grid
```

The baseline pair table selects the nearest valid VIIRS observation per MODIS
record. Ties are resolved by time difference and then stable VIIRS ID. The
candidate count and candidate table preserve the additional observations; no
VIIRS detections are reduced to a binary presence value. No heuristic
excellent/good categories are assigned.

`valid` means only that a pair meets the configured date, time, and distance
criteria; it is not a scientifically validated match.

The CSV outputs `modis_viirs_pairs.csv` and `modis_viirs_candidates.csv` are
written only when `save_overlap_tables` is called explicitly. Generated tables
are kept under the ignored `data/overlap/` directory by default. To run the
offline synthetic example:

```bash
python -m spatial_ml.pipeline.check_overlap_matching
```

The example fixtures are synthetic and are not NASA observations. Matching is
an observation-to-observation baseline, not a reconstruction of sensor pixel
footprints or proof of a fire-event relationship. Preserve timestamps for
chronological or event-based training/validation separation; do not randomly
split nearby records from the same event across training and validation. The
baseline model is implemented below, but no real-data validation or historical
synthesis has been performed.

## 375 m target grids and training samples

`LocalTargetGrid` creates a square metric grid centered at the MODIS
observation. Defaults in `spatial_ml/config.py` are 375 m resolution and
3 × 3 cells, covering 1,125 m × 1,125 m. Resolution, dimensions, and projection
strategy can be configured per grid. The default local azimuthal-equidistant
(AEQD) projection is centered at each MODIS location. A caller may instead
provide a projected custom CRS with meter units.

Rows increase north-to-south and columns west-to-east. The MODIS origin is at
the center of the default odd-sized grid. Geographic coordinates are projected
to local meters before cell assignment; the grid transform, CRS, bounds, and
index convention are preserved in metadata. The east/north edge convention is
left/top inclusive and right/bottom exclusive.

`build_viirs_target_grid` accepts one MODIS observation and its associated
VIIRS candidate rows. It reuses the overlap pipeline's `delta_x_m` and
`delta_y_m` for the default AEQD grid; with a custom CRS it projects the VIIRS
coordinates into that grid. Each VIIRS point is assigned a stable row/column.
Out-of-grid points remain in the mapping table with `in_grid=False` instead of
being silently dropped.

The primary target is a 2D binary observed-point occupancy grid: 0 means no
VIIRS point was mapped into that cell, and 1 means one or more points were
mapped there. Multiple VIIRS points in a cell are aggregated with logical OR;
their individual IDs, coordinates, FRP, and confidence remain in the target
observation table. An empty target means no in-grid VIIRS point evidence in the
provided candidate set, not proof that no fire occurred. The grid models the
locations of point detections; it is not the physical VIIRS pixel footprint or
a reconstructed satellite image.

## Environmental raster interface

`RasterFeatureSource` accepts caller-supplied local raster paths, feature
names, bands, and resampling methods. `extract_environmental_features` uses
rasterio reprojection/resampling to align each source to the same local grid
CRS, transform, width, and height. Continuous layers can use bilinear
resampling; categorical land cover should use nearest-neighbor resampling.
Nothing is downloaded automatically and no provider is assumed.

Missing raster pixels remain NaN by default (or can be assigned a caller
chosen fill value). The interface performs no global imputation or
normalization, so later preprocessing can be fit using training data only.
When an elevation feature is explicitly selected, slope is derived in degrees
from local-meter gradients. Downhill aspect is represented by `aspect_sin` and
`aspect_cos` to avoid the 0°/360° discontinuity; flat-cell aspect is missing.
Slope/aspect quality depends on the source DEM resolution and alignment.

## ML sample representation and leakage safeguards

`build_training_sample` combines:

- MODIS FRP/confidence features and observation metadata;
- an aligned H × W × C environmental feature array (zero channels when no
  environmental source is supplied);
- a 2D binary VIIRS target grid and the per-observation target mapping table.

The sample retains MODIS ID, datetime, coordinates, source sensor labels,
target-grid geometry, and VIIRS IDs. `TrainingSample.save/load` serializes
arrays and JSON metadata to compressed NPZ without pickle. Timestamps remain
available for later chronological or event-based splitting. Do not randomly
split neighboring samples across train and validation, or normalize using
validation/test data.

Tests create tiny deterministic rasters in temporary directories, using
synthetic values, CRS, and geotransforms; no raster fixture or NASA data is
downloaded or committed. Grid size, resolution, projection, matching radius,
and time tolerance are configurable starting assumptions, not scientifically
validated thresholds. Any historical output must be labeled MODEL-ESTIMATED
FINE-SCALE FIRE-DETECTION PROBABILITY + UNCERTAINTY, never as historical
observed VIIRS.
