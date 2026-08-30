"""Compatibility shim — ``import model_testing.*`` resolves to ``tools/ml/``."""
from pathlib import Path

__path__ = [str(Path(__file__).resolve().parent.parent / "tools" / "ml")]
