# Adopt the changelog practice

**Date:** 2026-08-11
**Type:** decision
**Author:** claude-code

## What changed

Seeded this repo's `changelog/` - a per-entry record of deliberate changes and the
reasoning behind them - and added a **Changelog** section to `CLAUDE.md`. Format and
rules are in `changelog/README.md`.

## Why

The homelab's `ops-log/` habit (agents recording investigations and referencing them
later) has been its most valuable institutional-memory practice, but that memory
did not exist in the app repos. Features and structural changes accumulated with the
*why* living only in commit messages nobody re-reads or in agent context that's gone
by the next session. This changelog captures the reasoning next to the code so future
work doesn't have to reverse-engineer it. Rolled out across all managed repos at
once.

## Related

- nottingham-cloud agent/changelog.md, issue #183
