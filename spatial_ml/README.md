# MODIS-to-VIIRS Spatial ML

## Purpose

This module will provide the foundation for estimating the sub-pixel
distribution of active-fire observations at VIIRS-like spatial resolution from
coarser MODIS observations. The current module includes local FIRMS CSV
ingestion and cleaning utilities. It does not implement or train a model.

## Scientific problem

MODIS and VIIRS observe active fires at different spatial resolutions. The
project aims to learn how MODIS fire observations relate to finer-scale
patterns observed by VIIRS, then apply that relationship to earlier MODIS data.
The eventual output is a model-derived spatial estimate, not a reconstruction
of observations that were never collected.

## MODIS and VIIRS spatial resolutions

- MODIS: approximately 1,000 m (1 km).
- VIIRS: approximately 375 m.

These are nominal resolutions; downstream processing will need to account for
the source products' geolocation, projection, and observation characteristics.

## Overlap-period training concept

The MODIS/VIIRS overlap begins in 2012. Paired observations from the overlap
period can provide training examples for learning sub-pixel spatial patterns.
The overlap end year is intentionally not assumed: set
`SPATIAL_ML_OVERLAP_END_YEAR` when a dataset-specific end year is selected.
No training examples are included in this repository setup.

## Environmental features

Environmental spatial context may include DEM/elevation and land cover.
Additional feature choices and processing methods remain to be determined.

## Planned model

The planned model will learn a relationship between coarse MODIS observations,
environmental features, and finer-scale fire patterns. The specific model
architecture has not been selected or implemented.

## Validation strategy

The future model will be evaluated against held-out real VIIRS observations
from the overlap period. No model has been trained or validated, and no
accuracy results are available at this stage.

## Historical 2000–2011 synthesis

MODIS provides the historical coarse-resolution observations for 2000–2011.
After development and evaluation with overlap-period observations, a validated
model may be applied to those historical MODIS observations to estimate a
375 m fire probability or density distribution.

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
products. In particular, the estimated 375 m values for 2000–2011 are MODEL
ESTIMATES generated from MODIS and spatial context. They must not be presented
or labeled as actual VIIRS observations.

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
future chronological splitting; do not randomly split nearby records from the
same event across training and validation. This milestone only prepares
observed overlap data for later work: it does not create 375 m rasters,
environmental features, model estimates, training, or validation results.
