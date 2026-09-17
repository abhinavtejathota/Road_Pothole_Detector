# Agent mode scope

- Implement only the requested change; stop when done.
- No drive-by refactors, dependency upgrades, or "while I'm here" cleanups.
- Skip tests/linters/builds unless the user asks or the change clearly requires a smoke check.
- Prefer editing existing modules over creating new scripts/helpers.
- Against Supabase: never spin multi-service or enlarge DB pools.
