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
or model-performance results. The generic `run_local` command is intended for
small CSV inputs and does not attest that user-supplied files are authentic
NASA observations. Use the real-archive workflow below for the chunked
M2-to-M4 path and its provenance manifest. No conclusions about model
performance should be drawn until real held-out evaluations have completed.
The evaluation workflow does not produce historical estimates.

### Real FIRMS archive processing and M7 training

`spatial_ml.pipeline.real_data` accepts the local MODIS Collection 6.1 and
Suomi-NPP VIIRS Collection 2 ZIP archives. It reads CSV members from ZIP
archives in chunks, writes normalized compressed intermediates, combines
annual boundary dates by source-row fingerprint, then runs the existing M3
matcher one acquisition date at a time. Monthly compressed M3 candidate and
pair tables and pickle-free M4 sample shards are written with provenance and
processing manifests. The original ZIP files are not changed. Outputs are
resumable by completed month; a partial normalized M2 run is not silently
overwritten.

Example (substitute local archive paths; no data are downloaded):

```bash
python -m spatial_ml.pipeline.real_data \
  --archives /local/path/DL_FIRE_M-C61_849514.zip \
    /local/path/DL_FIRE_SV-C2_849515.zip \
    /local/path/DL_FIRE_M-C61_849541.zip \
    /local/path/DL_FIRE_SV-C2_849542.zip \
    /local/path/DL_FIRE_M-C61_849531.zip \
    /local/path/DL_FIRE_SV-C2_849532.zip \
    /local/path/DL_FIRE_M-C61_849545.zip \
    /local/path/DL_FIRE_SV-C2_849546.zip \
    /local/path/DL_FIRE_M-C61_849584.zip \
    /local/path/DL_FIRE_SV-C2_849585.zip \
  --processed-dir data/processed/firms_real_2016_2021 \
  --output-dir data/overlap/real_2016_2021 \
  --chunk-size 100000
```

The M7 real-data runner samples M4 parent samples without replacement,
stratified by calendar year (default maximum 10,000 parents per year), and
records the eligible and selected counts plus input-shard hashes. By default
it requests temporal, spatial-block, and combined held-out evaluations. The
temporal split trains through 2018, validates on 2019, and tests on 2020–2021;
spatial and combined splits use 500 km equal-area blocks. These are explicit
evaluation configurations, not scientific matching thresholds. Unavailable
splits are reported rather than weakened.

```bash
python -m spatial_ml.evaluation.train_real \
  --sample-dir data/overlap/real_2016_2021/samples \
  --results-dir results/real_m7_2016_2021 \
  --models-dir models/real_2016_2021
```

M5 and M6 are trained on the same training parents, with training-only
preprocessing. Calibration is fit on validation predictions; model comparison
and reported performance use held-out parents. This runner does not attest
NASA authenticity merely from file paths. Its M4 sampling frame contains
MODIS parents with positive in-grid observed VIIRS candidate evidence; it
does not convert unmatched parents to negative labels. Results therefore
describe that eligible sample population, not all MODIS observations.
Environmental raster channels are currently unavailable when no local rasters
are supplied. Treat these runs as preliminary evaluation until provenance,
sampling representativeness, split coverage, and environmental limitations
have been reviewed.

The ten local annual archives currently available cover **2016-11-14 through
2021-11-14**, not the full requested team period through 2026-07-31. The M2
audit counted 124,276,227 source rows and retained 124,060,012 after removing
216,215 exact source-row fingerprint duplicates, including genuine repeats
across shared annual boundary dates. It found no invalid coordinates or
timestamps and no empty source fields. Four invalid FRP values were flagged
and normalized to missing without dropping their observation rows. Terra and
Aqua MODIS detections are both present; the VIIRS archives identify SNPP.
MODIS confidence remains numeric while VIIRS confidence remains categorical
(`l`/`n`/`h`).

## Historical MODIS 2000–2011 estimation

The historical pipeline applies a saved M7 spatial ensemble to local MODIS
FIRMS observations. It writes **MODEL-ESTIMATED FINE-SCALE FIRE-DETECTION
PROBABILITY + UNCERTAINTY**. It does not reconstruct historical VIIRS
observations, estimate a physical VIIRS pixel value, or produce a binary
confirmed-fire product.

### Model selection and artifacts

The pipeline never trains on historical observations and never chooses a
model simply because it is available. It currently loads only the serialized
`ProbabilisticSpatialEnsemble`; the Random Forest has no compatible saved-model
interface. A CNN can be selected as scientifically supported only when an M7
report explicitly attests to real observed FIRMS data, compares both models on
the identical held-out population, matches the saved model's training parents,
feature schema, configuration, and artifact SHA-256, and the selected model
has strictly lower Brier score and log loss with no worse expected calibration
error. A valid
attested report that does not select the CNN blocks its use; the exploratory
flag cannot override that evidence.

The M7 local evaluation command saves `spatial_ensemble.pt` with the exact
fitted preprocessing and training provenance alongside its reports. An M8
artifact is rejected if its preprocessing version, grid provenance, feature
order, or training-parent metadata is incompatible. The optional
`spatial_ensemble_calibrator.json` is used only when its saved provenance says
it was fitted on validation parents disjoint from model-training parents. When
a matching M7 report is supplied, the model and calibrator SHA-256 hashes,
calibration method, and validation IDs must also match that report. A
calibration artifact cannot be used without its matching M7 report. The
repository currently has no real-data model artifact,
calibration artifact, or real held-out selection evidence.

If no valid real-data selection evidence exists, running requires the explicit
`--allow-exploratory` option. Exploratory outputs are marked
`exploratory_unvalidated`; this label does not mean the model is scientifically
valid. The M7 report generated by the current local workflow does not itself
attest that user-supplied files are real NASA observations. Do not add an
attestation unless the data provenance and held-out evaluation have actually
been reviewed.

### Supplying historical FIRMS observations

Download or otherwise obtain the historical global MODIS FIRMS CSV separately
from NASA FIRMS and place the file locally, for example at
`data/raw/modis/historical_global.csv`. The M8 command does not download data.
Supply a CSV with latitude, longitude, a timestamp or acquisition date/time,
and satellite columns. Instrument, confidence, FRP, and a source record ID are
preserved when available. Terra coverage is checked from 2000-11-01 and Aqua
from 2002-07-01; invalid coordinates, timestamps, duplicates, unsupported
satellites/instruments, and pre-satellite dates are reported in the failure
file or QC counts. The source file hash, source row number, original
coordinates/timestamp, and source ID are retained in output records.

The saved model must be based on the same M7 feature schema. Supply
environmental rasters by their exact trained feature name (for example
`elevation_m` or `land_cover`). Missing trained environmental channels remain
missing and are passed through the model's saved preprocessing; unexpected
channels are rejected. The model's saved grid dimensions and resolution are
used to construct the same local modeled target cells as M7.

### Pilot and global commands

Run M7 evaluation first with local overlap data. Its default output directory
is `results/milestone_7`, including `metrics.json` and
`spatial_ensemble.pt`. Then run a small date/geographic pilot:

```bash
python -m spatial_ml.pipeline.historical \
  --modis data/raw/modis/historical_global.csv \
  --model-artifact results/milestone_7/spatial_ensemble.pt \
  --model-name spatial_ensemble \
  --allow-exploratory \
  --start-date 2000-11-01 \
  --end-date 2000-12-31 \
  --bbox="-124,32,-114,42" \
  --batch-size 500 \
  --output-dir outputs/historical_estimates/pilot
```

Add `--m7-evaluation-report results/milestone_7/metrics.json` only when
providing a report that meets the documented selection gate. Add
`--calibration-artifact results/milestone_7/spatial_ensemble_calibrator.json`
only when that validation-fitted artifact exists and is compatible. Optional
local rasters use repeatable `--environment FEATURE=PATH` options, such as
`--environment elevation_m=data/environmental/dem.tif`.

Global processing uses the same command without `--bbox`, with the full
historical date range and a new output directory:

```bash
python -m spatial_ml.pipeline.historical \
  --modis data/raw/modis/historical_global.csv \
  --model-artifact results/milestone_7/spatial_ensemble.pt \
  --model-name spatial_ensemble \
  --allow-exploratory \
  --start-date 2000-11-01 \
  --end-date 2011-12-31 \
  --batch-size 1000 \
  --output-dir outputs/historical_estimates/global
```

Omit `--allow-exploratory` only if a real, attested M7 report supports the
selected model. Processing is local, deterministic where model inference
allows, chunked by input batch, and does not silently download or fabricate
observations. `--resume` replays the same input/model/filter signature into
existing batch paths; it rejects changed inputs or settings.

### Outputs, uncertainty, and limitations

Each `batches/predictions-*.csv` row describes one modeled target cell and
includes its parent MODIS observation ID, modeled cell ID and center
coordinates, observation datetime, satellite/instrument, raw probability,
optional calibrated probability, uncertainty value/method, source provenance,
model/preprocessing/calibration versions, and explicit
`estimated=true` / `reference_observation_available=false` flags. A target
cell is not a physical VIIRS pixel. Failed records are written to
`failed_observations.csv`; `manifest.json` tracks batches and completion;
`quality_control.json` and `distribution_shift.json` summarize coverage,
failures, probability checks, training-support flags, feature shifts, and
missingness. `historical_processing_report.json` and `.md` describe the run.

The ensemble standard deviation is only a model-disagreement proxy; it is not
a confidence interval or calibrated statistical uncertainty. Probability
calibration is a separate transformation and does not calibrate uncertainty.
The historical period predates VIIRS overlap observations, so temporal,
geographic, sensor, FRP, confidence, and environmental distribution shifts can
make model predictions unreliable. Three-training-scale feature flags are
screening diagnostics, not guarantees of support. Global completeness is not
inferred from a bounding box or successful processing. No historical MODIS
observations or M8 predictions are currently present in this repository.

## Team integration exports

`spatial_ml.exports` provides four CSV products for integration. It reuses the
existing FIRMS loaders/cleaner, M3 observation IDs and candidate matcher, and
M8 prediction CSVs; it does not download data, rerun inference, aggregate
observations, or change the scientific target.

| Product | Default filename | Meaning |
| --- | --- | --- |
| `modis` | `modis_observations.csv` | Cleaned, observed MODIS records. |
| `viirs` | `viirs_observations.csv` | Cleaned, observed VIIRS records. |
| `matched` | `modis_viirs_matched.csv` | Every valid MODIS–VIIRS candidate relationship plus unmatched MODIS records. |
| `probabilities` | `fine_scale_fire_probability.csv` | Existing M8 model-estimated per-cell probability records. |

Observed-sensor CSVs include a stable normalized `observation_id`, the
original source identifier when provided, sensor-specific satellite and
instrument fields, source confidence/FRP values, source file/product/version,
ingestion timestamp, quality flags, and `source_fields_json`. Unrecognized
input columns are retained in that JSON object; normalized source columns that
are useful for re-import are also preserved. A stable M3-derived ID is not a
NASA source ID: an absent original identifier stays empty. MODIS and VIIRS
confidence/FRP fields are not asserted to be interchangeable, and FRP units
are exported only when the input contains them.

The matched table retains multiple candidate pairs. `selected_nearest_match`
marks the M3 nearest-pair choice without discarding other candidates.
Unmatched rows have an empty VIIRS ID and an explicit `match_status`; MODIS
records outside the observed date intersection are also retained with an
outside-period status. Distance is in meters and absolute temporal difference
is in seconds. The match status describes the configured M3 heuristic, not a
scientifically validated event association.

The fine-scale probability table maps M8 column names to a stable integration
schema and includes parent observation, target cell, observation time, cell
center and WGS84 polygon-boundary coordinates, raw and calibrated numeric
probabilities, uncertainty, source provenance, model/preprocessing/calibration
versions, spatial resolution, and CRS. The row key is
`(parent_modis_id, cell_id, observation_datetime)`: cells from different
observation times are never merged. Historical estimates remain
`estimated=true` and `reference_observation_available=false`; they are not
observed VIIRS detections or binary fire/no-fire labels.

CSV cell boundaries are derived only when the M8 `rNN_cNN` cell IDs, parent
coordinates, reported cell centers, and grid resolution confirm a complete
regular local grid. GeoJSON is optional and emits one valid polygon feature
per modeled cell, with coordinates in EPSG:4326 and the local grid CRS retained
in feature properties. Cells crossing the antimeridian are split into valid
MultiPolygon geometries. Inconsistent/incomplete grid geometry fails
validation instead of being guessed. The GeoJSON is a standards-compliant
WGS84 FeatureCollection; CRS metadata is carried as properties rather than a
legacy top-level `crs` member.

### Export CLI

The default requested integration coverage is **2021-11-14 through
2026-07-31**, configured by `TEAM_EXPORT_START_DATE` and
`TEAM_EXPORT_END_DATE`. These dates are filters, not claims that data are
available. Earlier data can be included by selecting earlier dates or using
`--all-dates`. For example, export observed source products and all match
candidates:

```bash
python -m spatial_ml.exports \
  --products modis viirs matched \
  --modis data/raw/modis/firms.csv \
  --viirs data/raw/viirs/firms.csv \
  --output-dir outputs/team_exports
```

Validate a requested source export without writing anything:

```bash
python -m spatial_ml.exports \
  --products modis \
  --modis data/raw/modis/firms.csv \
  --validation-only
```

Export previously generated M8 historical predictions for 2000–2011, with
optional polygons:

```bash
python -m spatial_ml.exports \
  --products probabilities \
  --probability-input outputs/historical_estimates/global/batches \
  --start-date 2000-11-01 \
  --end-date 2011-12-31 \
  --geojson \
  --output-dir outputs/team_exports/historical
```

`--probability-input` accepts one M8 CSV or a directory of
`predictions-*.csv` batches. `--geojson` applies only to `probabilities`.
Use `--all-dates` to remove the default date filter; do not combine it with
`--start-date` or `--end-date`. Selected source files must exist locally; the
CLI does not fetch NASA data. Missing files and invalid inputs produce clear
errors rather than success-shaped empty exports. Valid empty-after-filter
datasets produce header-only CSVs and manifest warnings.

An `export_manifest.json` accompanies each writing run. It records schema
version, generation time, output columns and row counts, date/geographic
coverage, source files, CRS, warnings, and validation checks. CSV values are
re-importable; structured original fields and source provenance are encoded
as JSON columns. The manifest reports observed extent only and does not prove
complete global coverage.

The current loaders and matcher operate on in-memory tables. For very large
global archives, run products on appropriately sized local files or regions;
this export layer does not yet provide streaming ingestion or matching.

Synthetic fixtures validate schemas, provenance retention, matching behavior,
filtering, probability bounds, and geometry construction. They are software
tests only and do not demonstrate real-world model accuracy. This checkout
does not contain the requested global FIRMS observations or M8 historical
prediction batches, so no production export or accuracy claim is available.

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

The standard `load_modis(path)` and `load_viirs(path)` functions continue to
accept local CSV files and return DataFrames. They do not download NASA data.
For large FIRMS ZIP archives, `spatial_ml.data.archive_ingest` streams the
single CSV member in bounded chunks without extracting it.

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
- `iter_firms_zip_chunks(path, chunksize=100_000)` yields that normalized
  schema plus `source_archive`, `source_file`, `source_row_number`, and a
  deterministic 128-bit source-row fingerprint. The fingerprint is derived
  from all original source columns; it is not a NASA source identifier.
  Brightness, scan, track, product version, and type remain available.
- The archive CLI can audit many archives and optionally write one cleaned,
  gzip-compressed normalized CSV per archive. It records quality summaries in
  JSON, refuses to overwrite an existing report or processed file, and never
  extracts or modifies the ZIPs. These processed files are in the ignored
  `data/processed/` tree.

For example, to audit and normalize the five matching MODIS/SNPP VIIRS archive
pairs, keeping the Desktop ZIPs unchanged:

```bash
python -m spatial_ml.data.archive_ingest \
  --archives \
  ~/Desktop/DL_FIRE_M-C61_849514.zip \
  ~/Desktop/DL_FIRE_SV-C2_849515.zip \
  ~/Desktop/DL_FIRE_M-C61_849541.zip \
  ~/Desktop/DL_FIRE_SV-C2_849542.zip \
  ~/Desktop/DL_FIRE_M-C61_849531.zip \
  ~/Desktop/DL_FIRE_SV-C2_849532.zip \
  ~/Desktop/DL_FIRE_M-C61_849545.zip \
  ~/Desktop/DL_FIRE_SV-C2_849546.zip \
  ~/Desktop/DL_FIRE_M-C61_849584.zip \
  ~/Desktop/DL_FIRE_SV-C2_849585.zip \
  --chunk-size 100000 \
  --report results/firms_archive_audit_rerun.json
```

Omit `--processed-output-dir` to audit/normalize in memory without saving
intermediate data. Add that option only when cleaned per-archive gzip
intermediates are needed. To use a different report or output location, pass
those paths explicitly. An existing output requires review and a new
destination; the CLI will not replace it.

The Desktop archive audit found 124,276,227 rows in the ten supplied archives;
124,060,012 remained after exact source-row
fingerprint duplicate handling. All archive date ranges matched their stated
inclusive periods, with 366 or 367 distinct dates and monotonic acquisition
date order. No invalid coordinates, dates/times, FRP/confidence values, or
missing values in the supplied source fields were found. The 216,215 duplicate
fingerprints occurred on adjacent archive boundaries; no records were removed
merely for sharing coordinates and timestamps. MODIS confidence remained
numeric (0–100); VIIRS confidence remained categorical (`l`, `n`, `h`). The
latest available date is 2021-11-14, not the requested team coverage end of
2026-07-31. Per-archive counts and distributions are in
`results/firms_archive_audit.json`. The ZIPs remain on the Desktop; no large
normalized intermediate was persisted by this audit.

- Date/time values are parsed into a date, an HHMM time string, and a datetime.
  Midnight and leading-zero acquisition times are supported. Missing or
  malformed timestamps remain missing during normalization and are excluded
  by cleaning with an informational log message.
- Cleaning removes rows with missing/out-of-range coordinates and rows with
  missing or malformed datetimes. Archive duplicates use the source-row
  fingerprint, so records sharing coordinates and timestamps but differing in
  other reported source values remain distinct. Non-archive DataFrames without
  fingerprints retain the legacy identifying-field duplicate behavior.
- Missing, non-numeric, non-finite, or negative FRP is represented as missing;
  those records are retained. The source confidence value is preserved; values
  outside the supported numeric range or known categorical labels are flagged
  in `confidence_valid`, not filtered. MODIS numeric confidence is not
  substituted for VIIRS categorical confidence. No confidence threshold is
  applied.
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
