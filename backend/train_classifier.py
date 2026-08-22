"""
train_classifier.py — one-command trainer for AI-1
===================================================
Trains satellite_fault_classifier_V2 on the repository's real orbital-element
datasets and writes the artifacts the pipeline needs.

Datasets used (all already in data/):
    data/input.csv        — 663 satellites (general catalog)
    data/input__1_.csv    —  91 CubeSats
    data/input__2_.csv    —  97 amateur radio satellites (OSCAR, ISS)

Produces model_artifacts/:
    transformer_encoder.pt   — Transformer encoder weights
    isolation_forest.pkl     — unsupervised anomaly detector
    scaler.pkl               — StandardScaler fitted on FEATURE_COLS
    meta.json                — CONFIG + FEATURE_COLS snapshot

Usage
-----
    python train_classifier.py                    # train on all 3 CSVs
    python train_classifier.py --epochs 50        # override epoch count
    python train_classifier.py --out_dir ./tmp    # alternate artifact dir
    python train_classifier.py --n2yo_api_key KEY # also pull live TLEs

Requires: torch, scikit-learn, pandas, numpy, tqdm
    pip install -r requirements.txt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for _p in (ROOT, ROOT / "pipeline"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

DATA_DIR = ROOT / "data"
#: Preferred input — the propagated, fault-injected series from
#: generate_dataset.py. Used automatically when present.
SYNTHETIC_CSV = DATA_DIR / "synthetic_orbital_series.csv"
DEFAULT_CSVS = [
    DATA_DIR / "input.csv",
    DATA_DIR / "input__1_.csv",
    DATA_DIR / "input__2_.csv",
]


def _check_dependencies() -> None:
    missing = []
    for mod, pkg in (
        ("torch", "torch"),
        ("sklearn", "scikit-learn"),
        ("pandas", "pandas"),
        ("numpy", "numpy"),
    ):
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        print("ERROR: missing required packages: " + ", ".join(missing))
        print("Install them with:\n  pip install " + " ".join(missing))
        sys.exit(1)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Train the DeadSat AI-1 fault classifier on real orbital data"
    )
    parser.add_argument(
        "--csv",
        nargs="+",
        default=None,
        help="Override the CSV datasets to train on",
    )
    parser.add_argument(
        "--out_dir",
        default=str(ROOT / "model_artifacts"),
        help="Where to write the trained artifacts",
    )
    parser.add_argument("--epochs", type=int, default=None, help="Override CONFIG epochs")
    parser.add_argument(
        "--target_per_class",
        type=int,
        default=400,
        help="Synthetic augmentation target per fault class",
    )
    parser.add_argument("--n2yo_api_key", default="", help="Optional live TLE refresh")
    args = parser.parse_args()

    _check_dependencies()

    import numpy as np
    import pandas as pd
    import torch

    import satellite_fault_classifier_V2 as clf

    # ── Resolve datasets ──────────────────────────────────────────────
    if args.csv:
        csv_paths = [Path(p) for p in args.csv]
    else:
        # Prefer the propagated series from generate_dataset.py. Falling back
        # to the raw CSVs silently would train on the one-epoch-per-satellite
        # snapshot, where REV_DELTA and ecc_delta are zero for 100% of rows and
        # three of the four label rules can never fire — 807 of 854 rows
        # collapse to SOFTWARE_BUG. Artifacts built from that are worthless,
        # and nothing downstream would say so.
        if SYNTHETIC_CSV.exists():
            csv_paths = [SYNTHETIC_CSV]
            print(f"[LOAD] using propagated series: {SYNTHETIC_CSV.name}")
        else:
            csv_paths = [p for p in DEFAULT_CSVS if p.exists()]
            if csv_paths:
                print("!" * 72)
                print("WARNING: training on the RAW SNAPSHOT CSVs.")
                print("  These hold one epoch per satellite, so REV_DELTA and")
                print("  ecc_delta are 0 for every row and 3 of the 4 fault rules")
                print("  cannot fire. Expect ~95% SOFTWARE_BUG and 0 SEU.")
                print("  Generate the propagated series first:")
                print("      python generate_dataset.py --propagator sgp4 --verify")
                print("!" * 72)

    if not csv_paths:
        print(f"ERROR: no CSV datasets found in {DATA_DIR}")
        print("Expected either:")
        print(f"  {SYNTHETIC_CSV.name}   (run: python generate_dataset.py)")
        print("  input.csv, input__1_.csv, input__2_.csv")
        return 1

    print("=" * 62)
    print("DeadSat Resurrection — AI-1 Classifier Training")
    print("=" * 62)
    for p in csv_paths:
        print(f"  dataset : {p.name}")
    print(f"  out_dir : {args.out_dir}")

    if args.epochs:
        clf.CONFIG["epochs"] = args.epochs
    print(f"  epochs  : {clf.CONFIG['epochs']}")
    print(f"  seq_len : {clf.CONFIG['seq_len']}   features: {len(clf.FEATURE_COLS)}")
    print("=" * 62 + "\n")

    np.random.seed(clf.CONFIG["random_seed"])
    torch.manual_seed(clf.CONFIG["random_seed"])

    # ── 1. Load ───────────────────────────────────────────────────────
    frames = [clf.load_csv_datasets([str(p) for p in csv_paths])]

    if args.n2yo_api_key:
        print("\n[N2YO] Fetching live TLEs ...")
        df_live = clf.fetch_n2yo_tle(args.n2yo_api_key, clf.CONFIG["norad_ids"])
        if not df_live.empty:
            frames.append(df_live)

    df_raw = pd.concat(frames, ignore_index=True)
    print(f"\n[LOAD] {len(df_raw)} raw rows")

    # ── 2. Clean ──────────────────────────────────────────────────────
    df_clean = clf.clean_orbital_data(df_raw)

    # ── 3. Label ──────────────────────────────────────────────────────
    # ORDER CHANGED (Phase 2 leak fixes). The old sequence — fit scaler and
    # Isolation Forest on everything, augment everything, then split at
    # random — leaked the test set into training three separate ways.
    # build_dataloaders() now splits by satellite FIRST, augments only the
    # training split, and fits the scaler only on it.
    df_labelled = clf.assign_fault_labels(df_clean)

    # ── 4. Split by satellite + augment train + fit scaler + window ────
    train_loader, val_loader, test_loader, scaler, train_df = clf.build_dataloaders(
        df_labelled, target_per_class=args.target_per_class
    )

    # ── 5. Isolation Forest on NORMAL rows of the TRAIN split only ─────
    train_sats = set(train_df["NORAD_CAT_ID"])
    iforest = clf.train_isolation_forest(
        df_labelled[df_labelled["NORAD_CAT_ID"].isin(train_sats)], scaler
    )

    # ── 6. Train ──────────────────────────────────────────────────────
    model, device = clf.train_model(train_loader, val_loader, len(clf.FEATURE_COLS))

    # ── 7. Evaluate + baselines + model card ──────────────────────────
    # The card is GENERATED from these metrics, never hand-written, so every
    # number in docs/MODEL_CARD.md is reproducible by rerunning this script.
    tf_metrics = clf.evaluate_model(model, test_loader, device)
    baselines = clf.run_baselines(train_loader, test_loader)
    split_info = {k: dict(v) for k, v in clf._LAST_SPLIT_INFO.items()}
    for k, ld in (("train", train_loader), ("val", val_loader), ("test", test_loader)):
        split_info.setdefault(k, {})["windows"] = len(ld.dataset)
    clf.write_model_card(tf_metrics, baselines, split_info)

    # ── 8. Save ───────────────────────────────────────────────────────
    clf.save_artifacts(model, iforest, scaler, args.out_dir)

    # ── 9. Verify the bridge can load what we just wrote ───────────────
    print("\n[VERIFY] Reloading artifacts through the AI-1 -> AI-2 bridge ...")
    from pipeline.classifier_inference import FaultClassifierInference

    bridge = FaultClassifierInference(args.out_dir).load()
    sample = df_clean[clf.FEATURE_COLS].values[: clf.CONFIG["seq_len"]].astype("float32")
    report = bridge.classify(sample, norad_id=28654)
    print(f"  raw_fault_class : {report['raw_fault_class']}")
    print(f"  fault_type      : {report['fault_type']}  (procedure_library key)")
    print(f"  confidence      : {report['confidence']:.2%}")
    print(f"  anomaly_flag    : {report['anomaly_flag']}")

    print("\nTraining complete. Now run the full pipeline:")
    print("  python run_pipeline.py --all\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
