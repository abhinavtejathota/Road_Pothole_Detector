#!/usr/bin/env bash
# Thin wrappers — prefer ./scripts/services.sh directly.
exec "$(cd "$(dirname "$0")" && pwd)/services.sh" start "$@"
