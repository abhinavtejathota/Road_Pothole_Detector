"""Compatibility shim — implementation lives in the db package (incl. private names)."""
import smartroad_path  # noqa: F401
import db as _db

globals().update({k: v for k, v in vars(_db).items() if not k.startswith("__")})
