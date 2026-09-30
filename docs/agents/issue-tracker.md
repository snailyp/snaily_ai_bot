# Issue tracker: GitHub

Issues and specs live in GitHub Issues for `snailyp/snaily_ai_bot`.
Use the `gh` CLI from this clone; it infers the repository from the
Git remote.

## Conventions

- Create: `gh issue create --title "..." --body "..."`
  Use a heredoc for multiline bodies.
- Read: `gh issue view <number> --comments`
  Fetch structured fields when needed with
  `gh issue view <number> --json number,title,body,labels,comments`.
- List: `gh issue list --state open --json number,title,body,labels`
  Apply appropriate label and state filters.
- Comment: `gh issue comment <number> --body "..."`
- Apply labels: `gh issue edit <number> --add-label "..."`
- Remove labels: `gh issue edit <number> --remove-label "..."`
- Close: `gh issue close <number> --comment "..."`

Read `docs/agents/triage-labels.md` for triage label mappings.

## Pull requests as a triage surface

**PRs as a request surface: no.**

## Skill terminology

- "Publish to the issue tracker": create a GitHub issue.
- "Fetch the relevant ticket": read the issue and its comments.

## Wayfinding operations

When using a wayfinding skill:

- Map: one issue labelled `wayfinder:map`, containing Notes,
  Decisions-so-far, and Fog.
- Child ticket: link it to the map as a GitHub sub-issue. If unavailable,
  use a task list in the map and `Part of #<map>` in the child.
  Label children `wayfinder:<type>` with type `research`, `prototype`,
  `grilling`, or `task`.
- Blocking: use native GitHub issue dependencies. If unavailable,
  put `Blocked by: #<n>, #<n>` at the top of the child body.
  A ticket is unblocked when every blocker is closed.
- Frontier: select the first open child in map order with no open
  blockers and no assignee.
- Claim: `gh issue edit <number> --add-assignee @me`.
- Resolve: comment with the answer, close the child, then append
  a summary and link to the map's Decisions-so-far.
