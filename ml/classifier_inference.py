"""
ml/classifier_inference.py — compatibility shim.

The implementation lives in ``models/classifier_inference.py``. This module
simply re-exports it so the legacy ``ml.classifier_inference`` import path
continues to resolve.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from models.classifier_inference import *  # noqa: F401,F403
from models.classifier_inference import (  # noqa: F401
    FAULT_KEY_MAP,
    PROCEDURE_KEYS,
    ArtifactsNotFoundError,
    FaultClassifierInference,
    get_classifier,
    normalise_fault_key,
)
