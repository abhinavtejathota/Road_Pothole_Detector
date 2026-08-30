"""Compatibility shim — implementation lives in the detector package (incl. private names)."""
import detector as _detector

globals().update({k: v for k, v in vars(_detector).items() if not k.startswith("__")})
