"""
ml/ — backwards-compatibility alias package.

The canonical location for the AI-1 modules in this repository is ``models/``.
The standalone model-pipeline drop shipped them under ``ml/``, so this package
re-exports the same objects to keep both import paths working:

    from ml.classifier_inference import normalise_fault_key      # legacy
    from models.classifier_inference import normalise_fault_key  # canonical

Prefer the ``models.`` path in new code.
"""

from models.classifier_inference import (  # noqa: F401
    FAULT_KEY_MAP,
    PROCEDURE_KEYS,
    ArtifactsNotFoundError,
    FaultClassifierInference,
    get_classifier,
    normalise_fault_key,
)

__all__ = [
    "FAULT_KEY_MAP",
    "PROCEDURE_KEYS",
    "ArtifactsNotFoundError",
    "FaultClassifierInference",
    "get_classifier",
    "normalise_fault_key",
]
