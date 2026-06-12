"""
=============================================================================
 AI-1 | Satellite Fault Classifier — INPUT / INFERENCE LAYER
 Loads the artifacts produced by satellite_fault_classifier_tle.py and runs
 fault analysis on user-supplied data, three ways:

   1) --norad <ID> --n2yo_api_key <KEY>
        Pulls the satellite's last `seq_len` epochs straight from N2YO live
        TLE (note: N2YO /tle/ returns the *current* TLE only, so this mode
        repeats the current TLE to fill the window — best used together
        with --csv for real history, see mode 3).

   2) --csv <file.csv> --norad <ID>
        Reads a CelesTrak/TLE-format CSV (same schema as input.csv /
        input__1_.csv / input__2_.csv), filters to one satellite, and uses
        its last `seq_len` epochs.

   3) --csv <file.csv> --norad <ID> --n2yo_api_key <KEY>
        Combines history from CSV with the freshest N2YO TLE appended as
        the most recent epoch — the most realistic "near real-time" mode.

   4) --manual feature1=val,feature2=val,...
        Power-user mode: directly supply one row of FEATURE_COLS values
        (repeated `seq_len` times) for a quick what-if check.

REQUIRES: model_artifacts/ produced by satellite_fault_classifier_tle.py
    - transformer_encoder.pt
    - isolation_forest.pkl
    - scaler.pkl
    - meta.json
=============================================================================

EXAMPLES
--------
# Analyse ISS using your CSV history
python satellite_fault_input_layer.py \
    --csv input.csv --norad 25544 \
    --artifacts_dir ./model_artifacts

# Analyse ISS using CSV history + freshest live TLE from N2YO
python satellite_fault_input_layer.py \
    --csv input.csv --norad 25544 \
    --n2yo_api_key YOUR_KEY \
    --artifacts_dir ./model_artifacts

# Pure live mode (no CSV)
python satellite_fault_input_layer.py \
    --norad 25544 --n2yo_api_key YOUR_KEY \
    --artifacts_dir ./model_artifacts

# Manual what-if
python satellite_fault_input_layer.py \
    --manual "MEAN_MOTION=14.5,ECCENTRICITY=0.06,INCLINATION=51.6,\
RA_OF_ASC_NODE=180,ARG_OF_PERICENTER=180,MEAN_ANOMALY=180,BSTAR=0.0002,\
MEAN_MOTION_DOT=0.00003,MEAN_MOTION_DDOT=0,TLE_AGE_HOURS=2,REV_DELTA=15" \
    --artifacts_dir ./model_artifacts
"""

import os
import json
import pickle
import argparse
import requests
import numpy as np
import pandas as pd
import torch

# Re-use the model class, TLE parser, feature list etc. from the training script
from satellite_fault_classifier_tle import (
    SatelliteFaultTransformer,
    parse_tle_lines,
    clean_orbital_data,
    load_csv_datasets,
    predict,
    FEATURE_COLS,
    IDX_TO_LABEL,
    CONFIG,
)


# ---------------------------------------------------------------------------
# ARTIFACT LOADING
# ---------------------------------------------------------------------------

def load_artifacts(artifacts_dir: str):
    with open(os.path.join(artifacts_dir, "meta.json")) as f:
        meta = json.load(f)

    with open(os.path.join(artifacts_dir, "scaler.pkl"), "rb") as f:
        scaler = pickle.load(f)
    with open(os.path.join(artifacts_dir, "isolation_forest.pkl"), "rb") as f:
        iforest = pickle.load(f)

    cfg = meta["config"]
    model = SatelliteFaultTransformer(
        n_features=len(meta["feature_cols"]),
        d_model=cfg["d_model"],
        nhead=cfg["nhead"],
        num_layers=cfg["num_layers"],
        dropout=cfg["dropout"],
        num_classes=cfg["num_classes"],
    )
    model.load_state_dict(torch.load(
        os.path.join(artifacts_dir, "transformer_encoder.pt"),
        map_location="cpu",
    ))
    model.eval()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)

    return model, iforest, scaler, meta, device


# ---------------------------------------------------------------------------
# INPUT MODE 1/2/3: CSV history (+ optional live N2YO row)
# ---------------------------------------------------------------------------

def build_window_from_csv(csv_paths: list, norad_id: int, seq_len: int,
                           n2yo_api_key: str = "") -> pd.DataFrame:
    df_raw = load_csv_datasets(csv_paths)
    df_raw = df_raw[df_raw["NORAD_CAT_ID"].astype(float) == float(norad_id)]

    if df_raw.empty:
        raise ValueError(f"NORAD_CAT_ID {norad_id} not found in supplied CSV(s).")

    # Optionally append the freshest live TLE for this satellite
    if n2yo_api_key:
        live = fetch_single_n2yo_tle(n2yo_api_key, norad_id)
        if live is not None:
            df_raw = pd.concat([df_raw, pd.DataFrame([live])], ignore_index=True)

    df_clean = clean_orbital_data(df_raw)
    df_sat = df_clean[df_clean["NORAD_CAT_ID"].astype(float) == float(norad_id)]
    df_sat = df_sat.sort_values("EPOCH")

    if len(df_sat) < seq_len:
        # Not enough history -> pad by repeating the earliest row
        pad_needed = seq_len - len(df_sat)
        pad_rows = pd.concat([df_sat.iloc[[0]]] * pad_needed, ignore_index=True)
        df_sat = pd.concat([pad_rows, df_sat], ignore_index=True)

    return df_sat.tail(seq_len)


# ---------------------------------------------------------------------------
# INPUT MODE 1: pure live N2YO (repeats current TLE seq_len times)
# ---------------------------------------------------------------------------

def fetch_single_n2yo_tle(api_key: str, norad_id: int) -> dict:
    url = f"{CONFIG['n2yo_base']}/tle/{norad_id}&apiKey={api_key}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    tle_str = data.get("tle", "")
    sat_name = data.get("info", {}).get("satname", f"NORAD-{norad_id}")
    lines = tle_str.replace("\r\n", "\n").split("\n")
    if len(lines) < 2:
        raise ValueError("N2YO returned an incomplete TLE.")

    return parse_tle_lines(sat_name, norad_id, lines[0], lines[1])


def build_window_from_live_only(api_key: str, norad_id: int, seq_len: int) -> pd.DataFrame:
    record = fetch_single_n2yo_tle(api_key, norad_id)
    df_raw = pd.DataFrame([record])
    df_clean = clean_orbital_data(df_raw)
    # Repeat the single live row to fill the sequence window.
    # TLE_AGE_HOURS / REV_DELTA will be ~0 for all rows (no history).
    df_window = pd.concat([df_clean] * seq_len, ignore_index=True)
    return df_window


# ---------------------------------------------------------------------------
# INPUT MODE 4: manual feature dictionary
# ---------------------------------------------------------------------------

def build_window_from_manual(manual_str: str, seq_len: int) -> pd.DataFrame:
    """
    manual_str: "FEATURE1=val1,FEATURE2=val2,..."
    Any FEATURE_COLS not supplied default to 0.0.
    The same row is repeated seq_len times.
    """
    values = {col: 0.0 for col in FEATURE_COLS}
    for pair in manual_str.split(","):
        pair = pair.strip()
        if not pair:
            continue
        key, val = pair.split("=")
        key = key.strip().upper()
        if key not in FEATURE_COLS:
            raise ValueError(f"Unknown feature '{key}'. Valid features: {FEATURE_COLS}")
        values[key] = float(val)

    row = pd.DataFrame([values])
    df_window = pd.concat([row] * seq_len, ignore_index=True)
    return df_window


# ---------------------------------------------------------------------------
# ANALYSIS / REPORT
# ---------------------------------------------------------------------------

def run_analysis(window_df: pd.DataFrame, model, iforest, scaler, device, label="satellite"):
    window = window_df[FEATURE_COLS].values.astype(np.float32)
    anomaly_flag, fault_class, confidence = predict(window, model, iforest, scaler, device)

    print("\n" + "=" * 60)
    print(f" FAULT ANALYSIS REPORT — {label}")
    print("=" * 60)
    print(f"  Window size           : {window.shape[0]} epochs")
    print(f"  Latest epoch features :")
    last_row = window_df[FEATURE_COLS].iloc[-1]
    for col in FEATURE_COLS:
        print(f"    {col:<20s}: {last_row[col]:.6g}")
    print("-" * 60)
    print(f"  Isolation Forest      : {'ANOMALY' if anomaly_flag else 'normal'}")
    print(f"  Predicted fault class : {fault_class}")
    print(f"  Confidence            : {confidence:.2%}")
    print("=" * 60)

    interpretation = {
        "SEU":                  "Possible Single Event Upset (radiation-induced bit "
                                 "flip) — sudden orbital-eccentricity jump detected.",
        "SOFTWARE_BUG":         "Possible onboard software/propagation bug — "
                                 "revolution counter stuck or rolled back.",
        "FIRMWARE_CORRUPTION":  "Possible firmware/flash corruption — drag/decay "
                                 "coefficients (BSTAR / MEAN_MOTION_DOT) abnormal.",
        "COMMAND_INJECTION":    "Possible comms/uplink anomaly — TLE/ephemeris is "
                                 "stale beyond the configured threshold.",
    }
    print(f"\n  Interpretation: {interpretation.get(fault_class, 'Unknown')}")
    if not anomaly_flag and fault_class != "":
        print("  Note: Isolation Forest sees this as within-normal-range; "
              "treat the fault prediction with lower confidence.")

    return {
        "anomaly": bool(anomaly_flag),
        "fault_class": fault_class,
        "confidence": confidence,
    }


# ---------------------------------------------------------------------------
# MAIN — INPUT LAYER ENTRY POINT
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Satellite Fault Classifier — Input Layer")
    parser.add_argument("--csv", nargs="+", default=[],
                         help="CelesTrak/TLE CSV file(s) for satellite history "
                              "(e.g. input.csv input__1_.csv input__2_.csv)")
    parser.add_argument("--norad", type=int, default=None,
                         help="NORAD catalog ID of the satellite to analyse")
    parser.add_argument("--n2yo_api_key", type=str, default=os.environ.get("N2YO_API_KEY", ""),
                         help="N2YO API key for live TLE fetch")
    parser.add_argument("--manual", type=str, default="",
                         help='Manual feature input: "FEATURE=val,FEATURE=val,..."')
    parser.add_argument("--artifacts_dir", type=str, default="./model_artifacts",
                         help="Directory containing trained model artifacts")
    args = parser.parse_args()

    model, iforest, scaler, meta, device = load_artifacts(args.artifacts_dir)
    seq_len = meta["config"]["seq_len"]

    # --- Mode dispatch --------------------------------------------------
    if args.manual:
        window_df = build_window_from_manual(args.manual, seq_len)
        label = "manual input"

    elif args.csv and args.norad is not None:
        window_df = build_window_from_csv(args.csv, args.norad, seq_len, args.n2yo_api_key)
        label = f"NORAD {args.norad} (CSV history" + \
                (" + live N2YO)" if args.n2yo_api_key else ")")

    elif args.norad is not None and args.n2yo_api_key:
        window_df = build_window_from_live_only(args.n2yo_api_key, args.norad, seq_len)
        label = f"NORAD {args.norad} (live N2YO only)"

    else:
        parser.error(
            "Provide one of:\n"
            "  --csv <files> --norad <ID>\n"
            "  --norad <ID> --n2yo_api_key <KEY>\n"
            "  --manual \"FEATURE=val,...\""
        )
        return

    run_analysis(window_df, model, iforest, scaler, device, label=label)


if __name__ == "__main__":
    main()
