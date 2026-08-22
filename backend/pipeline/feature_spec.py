"""
feature_spec.py — canonical, dependency-free AI-1 specification
================================================================
Single source of truth for the classifier's configuration, feature set and
label mapping.

This module deliberately imports NOTHING beyond the standard library. That
matters because three different consumers need these constants:

  1. satellite_fault_classifier_V2.py  — training + inference (needs torch)
  2. models/classifier_inference.py    — the AI-1 -> AI-2 bridge
  3. pipeline.py / test_integration.py — orchestration and tests

Previously these constants lived inside satellite_fault_classifier_V2.py,
which imports torch, pandas and scikit-learn at module scope. That made the
entire pipeline — including the pure-logic fault-key normalisation — unusable
on a machine without a full ML stack installed.

satellite_fault_classifier_V2.py now imports from here and re-exports the
same names, so every existing `from satellite_fault_classifier_V2 import
CONFIG, FEATURE_COLS` continues to work unchanged.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------
CONFIG: dict = {
    # N2YO live refresh targets (NORAD IDs). Add/remove as needed.
    "norad_ids": [25544, 7530, 27844, 14129, 33591],
    "n2yo_base": "https://api.n2yo.com/rest/v1/satellite",

    # --- Fault thresholds (tuned for orbital-element data) ---------------
    "tle_age_stale_hours": 72.0,       # TLE older than this -> COMMS / GROUND SEGMENT issue
    "eccentricity_jump_threshold": 0.01,   # sudden change in orbital eccentricity
    "bstar_anomaly_threshold": 0.005,      # abnormal drag term (decay/attitude fault)
    "mean_motion_dot_threshold": 0.001,    # abnormal orbital decay rate
    "rev_gap_threshold": 50,               # missing revolutions between epochs

    # --- Model -------------------------------------------------------------
    "seq_len": 8,                       # time-steps (epochs) per sample window
    "d_model": 64,
    "nhead": 4,
    "num_layers": 2,
    "dropout": 0.1,
    "num_classes": 4,                   # SEU / SW_BUG / FW_CORRUPT / CMD_INJECT

    # --- Training ------------------------------------------------------------
    "batch_size": 32,
    "epochs": 30,
    "lr": 1e-3,
    "test_size": 0.2,
    "val_size": 0.1,
    "random_seed": 42,

    # --- Isolation Forest ----------------------------------------------------
    "if_contamination": 0.05,
    "if_n_estimators": 100,
}

# ---------------------------------------------------------------------------
# LABELS
# ---------------------------------------------------------------------------
FAULT_LABELS: dict[str, int] = {
    "SEU": 0,
    "SOFTWARE_BUG": 1,
    "FIRMWARE_CORRUPTION": 2,
    "COMMAND_INJECTION": 3,
}
IDX_TO_LABEL: dict[int, str] = {v: k for k, v in FAULT_LABELS.items()}

# ---------------------------------------------------------------------------
# FEATURES
# Orbital elements (replaces the V1 voltage/current/RSSI telemetry columns).
# Order is significant — windows are built and scaled in exactly this order.
# ---------------------------------------------------------------------------
FEATURE_COLS: list[str] = [
    "MEAN_MOTION",          # revs/day - orbital speed
    "ECCENTRICITY",         # orbit shape (0 = circular)
    "INCLINATION",          # orbital plane tilt (deg)
    "RA_OF_ASC_NODE",       # right ascension of ascending node (deg)
    "ARG_OF_PERICENTER",    # argument of perigee (deg)
    "MEAN_ANOMALY",         # position in orbit (deg)
    "BSTAR",                # drag term
    "MEAN_MOTION_DOT",      # 1st derivative of mean motion (decay rate)
    "MEAN_MOTION_DDOT",     # 2nd derivative of mean motion
    "TLE_AGE_HOURS",        # derived: hours since EPOCH (data-staleness proxy)
    "REV_DELTA",            # derived: change in REV_AT_EPOCH between consecutive rows
]

__all__ = ["CONFIG", "FAULT_LABELS", "IDX_TO_LABEL", "FEATURE_COLS"]
