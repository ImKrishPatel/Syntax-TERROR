# MODIS-to-VIIRS Spatial ML

## Purpose

This module will provide the foundation for estimating the sub-pixel
distribution of active-fire observations at VIIRS-like spatial resolution from
coarser MODIS observations. This milestone establishes configuration, package
structure, and directories only; it does not implement or train a model.

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
