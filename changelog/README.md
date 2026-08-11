# Changelog

This directory is the institutional memory for **deliberate changes** to this repo:
features added or reworked, infrastructure and structural changes, deprecations and
removals. One file per change. The load-bearing part of every entry is the **why** -
the problem it solved or the constraint that motivated it - so a future agent or
Fisher can reconstruct *why the thing is built the way it is* without re-deriving it
from a diff.

## When to write an entry

Write one when you:

- Add or meaningfully change a feature.
- Reorganize the app's structure, deploy model, or how components fit together.
- Deprecate or remove something.
- Make a decision whose *why* a future agent would otherwise have to reverse-engineer.

Skip it for: routine dependency bumps, formatting, trivial fixes, and version bumps
with no reasoning worth keeping. The test: **would someone later ask "why is it like
this / when did we add this and why?"** If yes, log it.

## Format

One file per change, named `YYYY-MM-DD-short-slug.md` (2-5 words, hyphenated,
lowercase).

```markdown
# <Title - the change in a few words>

**Date:** YYYY-MM-DD
**Type:** feature | infra | refactor | removal | decision
**Version:** vX.Y.Z (if it shipped in a tagged release; omit otherwise)
**Author:** claude-code | bot | fisher

## What changed

One or two sentences. The concrete change.

## Why

The motivating reason - the problem it solves, the user need, the constraint. This
is the point of the entry. Explain the reasoning, not just the mechanics.

## Context / alternatives

What else was considered and why this path won. Tradeoffs accepted. Skip if the
change was obvious and had no real alternative.

## Related

- Issue/PR #N, tag vX.Y.Z
- changelog/YYYY-MM-DD-related-entry.md
- Cross-repo links are fine. The record for a change lives in the repo where the
  change was made; reference across repos rather than duplicating.
```

Keep it lean. A terse, honest **Why** beats a long entry nobody writes.

## Committing entries

The changelog entry lands **with the change it describes** - same branch/PR (or same
commit) as the code. The why should arrive with the work, not as a later chore. When
a change ships in a release, note the version in the entry.

## Reading the log

```sh
ls changelog/                    # scan by date and slug
grep -rl "keyword" changelog/    # find entries by topic
```

Before reworking or removing something, check whether an entry explains why it's
built the way it is. The goal is to accumulate the *why*, not rediscover it.

---

This practice is homelab-wide: every repo Fisher's homelab manages keeps a
`changelog/`. The convention lives in `nottingham-cloud` at `agent/changelog.md`.
Operational incidents on deployed apps are investigated in nottingham-cloud's
`ops-log/`, not here - this log is for *why the code and structure are the way they
are*.
