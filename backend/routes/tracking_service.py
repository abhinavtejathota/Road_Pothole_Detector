"""Compatibility shim — implementation lives in routes.tracking (incl. private names)."""
import routes.tracking as _tracking

globals().update({k: v for k, v in vars(_tracking).items() if not k.startswith("__")})
