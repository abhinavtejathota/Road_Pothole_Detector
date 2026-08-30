"""Compatibility shim — implementation lives in routes.survey (incl. private names)."""
import routes.survey as _survey

globals().update({k: v for k, v in vars(_survey).items() if not k.startswith("__")})
