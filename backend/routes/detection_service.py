"""Compatibility shim — implementation lives in routes.detection (incl. private names)."""
import routes.detection as _detection

globals().update({k: v for k, v in vars(_detection).items() if not k.startswith("__")})
