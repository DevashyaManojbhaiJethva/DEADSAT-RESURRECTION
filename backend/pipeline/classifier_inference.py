"""
classifier_inference.py — AI-1 → AI-2 Integration Bridge
=========================================================
The single place that touches both sides of the classifier/agent interface.

  AI-1 side  — loads transformer_encoder.pt / isolation_forest.pkl / scaler.pkl
               from model_artifacts/ and calls satellite_fault_classifier_V2.predict()

  AI-2 side  — produces a fault_report dict in the exact schema that
               RecoveryAgent.run() and procedure_library.json expect


CRITICAL FIX — fault key normalisation
---------------------------------------
The classifier emits fault classes in UPPER_SNAKE_CASE:
    "SEU", "SOFTWARE_BUG", "FIRMWARE_CORRUPTION", "COMMAND_INJECTION"

procedure_library.json keys are lower_snake_case (except SEU):
    "SEU", "software_bug", "firmware_corruption", "command_injection"

recovery_agent.py does a direct dict lookup:
    fault_entry = library["procedures"][fault_key]

So an unnormalised "SOFTWARE_BUG" raises KeyError and recovery fails silently.
This bridge applies the canonical map before handing off to the agent.


WHY V2 AND NOT V1
------------------
This bridge targets satellite_fault_classifier_V2 (orbital-element edition).
V2 is the only classifier exposing FEATURE_COLS — the 11 GP/TLE orbital columns
that the pipeline's window builder produces. V1 is telemetry-column based
(TELEMETRY_COLS) and has no compatible window contract.
"""

from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path
from typing import Optional, Union

import numpy as np

# ── Path setup: make models/ and project root importable ──────────────
_MODELS_DIR = Path(__file__).resolve().parent
_ROOT = _MODELS_DIR.parent
for _p in (_MODELS_DIR, _ROOT, _ROOT / "emulator", _ROOT / "agents"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# The spec (CONFIG / FEATURE_COLS / labels) is dependency-free and always
# importable. The model class and predict() live in the training module, which
# pulls in torch + sklearn + pandas — imported lazily inside load() so that the
# normalisation logic, the pipeline's window builder and the test suite all
# work on a machine without the ML stack installed.
from feature_spec import CONFIG, FAULT_LABELS, FEATURE_COLS, IDX_TO_LABEL  # noqa: E402,F401


def _torch_available() -> bool:
    try:
        import torch  # noqa: F401

        return True
    except ImportError:
        return False


# ──────────────────────────────────────────────────────────────────────
# FAULT TYPE NORMALISATION MAP
# classifier output  ->  procedure_library.json key
# ──────────────────────────────────────────────────────────────────────
FAULT_KEY_MAP: dict[str, str] = {
    "SEU": "SEU",  # unchanged — the library key is literally "SEU"
    "SOFTWARE_BUG": "software_bug",
    "FIRMWARE_CORRUPTION": "firmware_corruption",
    "COMMAND_INJECTION": "command_injection",
    "NONE": "none",
    "NOMINAL": "none",
}

#: Reverse view used by the pipeline to state what it *expects* back.
PROCEDURE_KEYS: tuple[str, ...] = (
    "SEU",
    "software_bug",
    "firmware_corruption",
    "command_injection",
)


def normalise_fault_key(raw_fault: str) -> str:
    """
    Convert a classifier fault class to a procedure_library.json key.

    Accepts UPPER_SNAKE_CASE (current classifier output), already-normalised
    lower_snake_case, or mixed case. Unknown values pass through unchanged
    with a warning so a misconfiguration is loud rather than silent.
    """
    if raw_fault is None:
        return "none"

    raw = str(raw_fault).strip()

    mapped = FAULT_KEY_MAP.get(raw)
    if mapped:
        return mapped

    # Case-insensitive fallback: handles "seu", "Software_Bug", etc.
    lower_map = {k.lower(): v for k, v in FAULT_KEY_MAP.items()}
    mapped = lower_map.get(raw.lower())
    if mapped:
        return mapped

    print(f"[Bridge] WARNING: unknown fault class {raw!r} — passing through unmodified")
    return raw


# ──────────────────────────────────────────────────────────────────────
# ARTIFACT LOADER + INFERENCE
# ──────────────────────────────────────────────────────────────────────


class ArtifactsNotFoundError(FileNotFoundError):
    """Raised when model_artifacts/ is missing or incomplete."""


class FaultClassifierInference:
    """
    Loads trained classifier artifacts and exposes
    ``classify(telemetry_window) -> fault_report``.

    telemetry_window: np.ndarray of shape (seq_len, n_features) — the last
    ``CONFIG['seq_len']`` orbital-element rows for this satellite, in
    FEATURE_COLS order.
    """

    REQUIRED_ARTIFACTS = (
        "transformer_encoder.pt",
        "isolation_forest.pkl",
        "scaler.pkl",
    )

    def __init__(self, artifacts_dir: Optional[Union[str, Path]] = None):
        self.artifacts_dir = Path(artifacts_dir or (_ROOT / "model_artifacts"))
        self._device = None
        self._model = None
        self._iforest = None
        self._scaler = None
        self._meta = None
        self._loaded = False
        self._torch = None
        self._predict_fn = None

    # ── Introspection ─────────────────────────────────────────────────

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def artifacts_available(self) -> bool:
        """True if every required artifact file is present on disk."""
        return self.artifacts_dir.exists() and all(
            (self.artifacts_dir / name).exists() for name in self.REQUIRED_ARTIFACTS
        )

    def missing_artifacts(self) -> list[str]:
        if not self.artifacts_dir.exists():
            return list(self.REQUIRED_ARTIFACTS)
        return [
            name
            for name in self.REQUIRED_ARTIFACTS
            if not (self.artifacts_dir / name).exists()
        ]

    # ── Loading ───────────────────────────────────────────────────────

    def load(self) -> "FaultClassifierInference":
        """Load all artifacts. Call once before classify()."""
        if not _torch_available():
            raise ImportError(
                "PyTorch is required for classifier inference but is not installed.\n"
                "  pip install -r requirements.txt\n"
                "Until then the pipeline runs with --skip-classifier."
            )

        # Deferred: importing this pulls in torch, sklearn and pandas.
        import torch

        from satellite_fault_classifier_V2 import SatelliteFaultTransformer
        from satellite_fault_classifier_V2 import predict as _raw_predict

        self._torch = torch
        self._predict_fn = _raw_predict

        ad = self.artifacts_dir
        missing = self.missing_artifacts()
        if missing:
            raise ArtifactsNotFoundError(
                f"Missing classifier artifacts in {ad}: {', '.join(missing)}\n"
                "Train the classifier first:\n"
                "  python train_classifier.py\n"
                "or run the pipeline with --skip-classifier."
            )

        self._device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        # Meta (config snapshot written by save_artifacts)
        meta_path = ad / "meta.json"
        if meta_path.exists():
            with open(meta_path) as f:
                self._meta = json.load(f)
        else:
            self._meta = {"config": CONFIG, "feature_cols": FEATURE_COLS}

        feature_cols = self._meta.get("feature_cols", FEATURE_COLS)
        n_features = len(feature_cols)
        cfg = self._meta.get("config", CONFIG)

        if list(feature_cols) != list(FEATURE_COLS):
            print(
                "[Bridge] WARNING: artifact feature_cols differ from the current "
                "FEATURE_COLS. Artifacts may be stale — consider retraining."
            )

        # Transformer encoder
        self._model = SatelliteFaultTransformer(
            n_features=n_features,
            d_model=cfg.get("d_model", CONFIG["d_model"]),
            nhead=cfg.get("nhead", CONFIG["nhead"]),
            num_layers=cfg.get("num_layers", CONFIG["num_layers"]),
            dropout=cfg.get("dropout", CONFIG["dropout"]),
            num_classes=cfg.get("num_classes", CONFIG["num_classes"]),
        ).to(self._device)
        self._model.load_state_dict(
            torch.load(ad / "transformer_encoder.pt", map_location=self._device)
        )
        self._model.eval()

        # Isolation Forest + scaler
        with open(ad / "isolation_forest.pkl", "rb") as f:
            self._iforest = pickle.load(f)
        with open(ad / "scaler.pkl", "rb") as f:
            self._scaler = pickle.load(f)

        self._loaded = True
        print(f"[Bridge] Classifier artifacts loaded from {ad} (device={self._device})")
        return self

    # ── Core inference ────────────────────────────────────────────────

    def classify(
        self,
        telemetry_window: np.ndarray,
        norad_id: int = 28654,
        extra_detail: Optional[dict] = None,
    ) -> dict:
        """
        Run full classifier inference on an orbital-element window.

        Returns a fault_report dict ready for RecoveryAgent.run():
            {
              "fault_type":      str,   # normalised procedure_library key
              "fault_detail":    dict,
              "telemetry_frame": dict,  # last window row as a flat dict
              "confidence":      float,
              "norad_id":        int,
              "anomaly_flag":    bool,
              "raw_fault_class": str,   # pre-normalisation classifier output
            }
        """
        if not self._loaded:
            raise RuntimeError("Call .load() before .classify()")

        window = np.asarray(telemetry_window, dtype=np.float32)

        if window.ndim != 2:
            raise ValueError(
                f"Window must be 2-D (seq_len, n_features); got shape {window.shape}"
            )
        if window.shape[1] != len(FEATURE_COLS):
            raise ValueError(
                f"Window has {window.shape[1]} features; expected "
                f"{len(FEATURE_COLS)}: {FEATURE_COLS}"
            )
        if window.shape[0] != CONFIG["seq_len"]:
            raise ValueError(
                f"Window has {window.shape[0]} timesteps; expected "
                f"seq_len={CONFIG['seq_len']}"
            )
        if not np.isfinite(window).all():
            raise ValueError("Window contains NaN or Inf values")

        anomaly_flag, raw_fault_class, confidence = self._predict_fn(
            window, self._model, self._iforest, self._scaler, self._device
        )

        normalised_fault = normalise_fault_key(raw_fault_class)

        # Latest observation as a flat dict
        last_row = dict(zip(FEATURE_COLS, window[-1].tolist()))
        last_row["norad_id"] = norad_id

        fault_detail = {
            "raw_fault_class": raw_fault_class,
            "anomaly_flag": bool(anomaly_flag),
            "classifier": "satellite_fault_classifier_V2",
            "feature_snapshot": {
                "TLE_AGE_HOURS": last_row.get("TLE_AGE_HOURS", 0.0),
                "BSTAR": last_row.get("BSTAR", 0.0),
                "MEAN_MOTION_DOT": last_row.get("MEAN_MOTION_DOT", 0.0),
                "REV_DELTA": last_row.get("REV_DELTA", 0.0),
                "ECCENTRICITY": last_row.get("ECCENTRICITY", 0.0),
            },
            **(extra_detail or {}),
        }

        return {
            "fault_type": normalised_fault,
            "fault_detail": fault_detail,
            "telemetry_frame": last_row,
            "confidence": float(confidence),
            "norad_id": int(norad_id),
            "anomaly_flag": bool(anomaly_flag),
            "raw_fault_class": raw_fault_class,
        }

    # ── Convenience wrappers ──────────────────────────────────────────

    def classify_from_dataframe(self, df, norad_id: int = 28654) -> dict:
        """
        Accepts a DataFrame with FEATURE_COLS columns (>= CONFIG['seq_len']
        rows) and classifies the most recent window.
        """
        seq_len = CONFIG["seq_len"]
        if len(df) < seq_len:
            raise ValueError(f"DataFrame needs at least {seq_len} rows; got {len(df)}")
        window = df[list(FEATURE_COLS)].tail(seq_len).values.astype(np.float32)
        return self.classify(window, norad_id=norad_id)


# ──────────────────────────────────────────────────────────────────────
# Module-level singleton — avoids reloading artifacts per request
# (used by the FastAPI /pipeline endpoints)
# ──────────────────────────────────────────────────────────────────────

_SINGLETON: Optional[FaultClassifierInference] = None


def get_classifier(
    artifacts_dir: Optional[Union[str, Path]] = None,
) -> FaultClassifierInference:
    """Return a lazily-loaded, process-wide FaultClassifierInference."""
    global _SINGLETON
    if _SINGLETON is None or not _SINGLETON.is_loaded:
        _SINGLETON = FaultClassifierInference(artifacts_dir).load()
    return _SINGLETON


# ──────────────────────────────────────────────────────────────────────
# Smoke test
# ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== FaultClassifierInference — Smoke Test ===\n")

    test_cases = [
        ("SEU", "SEU"),
        ("SOFTWARE_BUG", "software_bug"),
        ("FIRMWARE_CORRUPTION", "firmware_corruption"),
        ("COMMAND_INJECTION", "command_injection"),
        ("seu", "SEU"),
        ("software_bug", "software_bug"),
    ]
    print("Fault key normalisation:")
    all_ok = True
    for raw, expected in test_cases:
        got = normalise_fault_key(raw)
        ok = got == expected
        all_ok &= ok
        print(f"  {raw:24s} -> {got:24s}  {'OK' if ok else 'FAIL'}")
    print(f"\nNormalisation: {'PASS' if all_ok else 'FAIL'}")

    bridge = FaultClassifierInference()
    print(f"\nArtifacts dir : {bridge.artifacts_dir}")
    print(f"Available     : {bridge.artifacts_available()}")
    if not bridge.artifacts_available():
        print(f"Missing       : {', '.join(bridge.missing_artifacts())}")
        print("\nTrain first:  python train_classifier.py")
        print("Then run:     python pipeline.py --all")
