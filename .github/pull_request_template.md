## Change

Describe what this pull request changes and why. State the behavior change a
user would notice and the security impact, including "none" when that is the
answer.

## Verification

- [ ] `pytest -p no:sugar` passes and coverage stays at 100 percent.
- [ ] `pre-commit run -a` passes (ruff, mypy, codespell, yamllint).
- [ ] The Validate and Security workflows pass on this branch.
- [ ] No secrets, tokens, vehicle identifiers, or personal addresses are
      committed, including in fixtures and test snapshots.
- [ ] New network behavior is documented and failure-bounded (timeouts,
      retry limits, and what happens when the Smartcar API is unavailable).
- [ ] Documentation under `docs/` is updated when behavior or configuration
      changes.

## Risk and rollback

State the risk of merging this and how an operator rolls back. The rollback
for a released change is installing the previous release from HACS.
