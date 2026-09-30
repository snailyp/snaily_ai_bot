# Domain docs

## Layout

This is a single-context repo:

- `CONTEXT.md` at the repository root: domain glossary and model.
- `docs/adr/`: architecture decision records.

## Before exploring

Read `CONTEXT.md` and ADRs relevant to the area being explored.

If these files do not exist, proceed silently without suggesting
their creation upfront. The `/domain-modeling` skill creates them
lazily when terms or decisions are resolved.

## Use the glossary's vocabulary

Use terms defined in `CONTEXT.md` in issue titles, proposals,
hypotheses, and test names. Respect its preferred terms.

If a needed concept is absent, reconsider whether it belongs to
the project's vocabulary or note the gap for `/domain-modeling`.

## Flag ADR conflicts

If a proposal contradicts an existing ADR, identify the ADR and
explain why its decision should be reopened.
