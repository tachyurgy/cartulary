# Cartulary

A symbol graph that answers "what breaks if I change this" — without merging same-named
functions or hanging on an import cycle.

Live: **https://cartulary.levelbrook.com**

Impact analysis is easy to get approximately right and hard to get right in the direction
that matters. Over-reporting produces noise. Under-reporting means a caller was missed, and
on a codebase nobody holds in their head, a missed caller is an incident.

Two hazards do most of the damage in real repositories. Both are present in the sample
corpus on the live page rather than assumed away.

## Shadowing

`billing.money.parse` and `ingest.dates.parse` are unrelated functions that happen to share
a name. Real codebases are full of this — `parse`, `clean`, `run`, `load`.

A graph keyed on the short name merges them, and the damage runs both ways. It invents
impact that does not exist, and — worse — it makes a genuine caller of one look like it was
already accounted for by the other.

So every symbol is keyed by fully qualified name, and a reference is resolved through the
**referring module's own import bindings** before anything else. `parse` inside
`billing.invoice` resolves to whatever `billing.invoice` imported or defined, never to a
same-named function elsewhere in the repository. Aliased imports (`from x import parse as
bparse`, `import billing.money as bm`) resolve correctly too.

On the live page, select each `parse` in turn. The impact sets are different.

## Cycles

Import cycles are common in code of any age. A naive transitive walk over reverse edges
never returns on one, and a walk that guards with a visited set only works if the guard is
checked *before* recursing.

Impact analysis here is iterative and breadth-first, so a cycle terminates — and cycle
membership is reported rather than silently absorbed, since a cycle is usually something
the caller wants to know about anyway. The corpus contains a three-module cycle
(`legacy.alpha` → `beta` → `gamma` → `alpha`) to show it.

## A bug this found

Building the corpus surfaced a real defect in the import handling. `import a.b` binds only
the **root** name `a`; the call site spells out `a.b.c()` in full. Binding the root to the
dotted path instead produces `a.b.b.c`, which resolves to nothing — so the edge is quietly
lost and the cycle went undetected while every test still passed.

That is exactly the under-reporting failure this project is about, so both the fix and two
regression tests are in the repo (`test_dotted_import_binds_only_the_root_name`,
`test_dotted_import_cycle_is_detected`).

Unresolvable references are also **kept** in an `unresolved` list rather than dropped, on
the same principle: silently discarding an edge you could not resolve is how the graph
starts lying.

## Tests

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest tests -q     # 22 tests
```

Both invariants were verified before being kept:

- Resolving by short name instead of fully qualified name fails
  `test_a_caller_is_attributed_to_the_right_parse` and
  `test_impact_of_one_parse_excludes_the_other_module`.
- Removing the visited check in `impact()` makes `test_a_cycle_terminates` **hang**, which
  is the honest demonstration — the naive version does not fail, it never finishes.

## API

```
GET /api/symbols            every symbol, with caller counts and short-name collisions
GET /api/impact?fqn=        direct, transitive, cycle membership, depth per affected symbol
GET /api/source?module=
GET /up
```

## Limits

This analyses Python via `ast`, one language, statically. It does not resolve dynamic
dispatch, `getattr`, decorators that rewrite call targets, or anything reached through a
plugin registry — all of which are exactly where static impact analysis under-reports, and
all of which need either type inference or runtime tracing to close. Method resolution
handles `self.method()` within a class but does not walk base classes, so an inherited
method call is currently attributed to the declaring class only. The corpus is nine small
in-memory modules, not a repository checkout: the graph structure and the resolution rules
are what this demonstrates, not the scale.

MIT.
