# Scored Web Search

Web-search skill to provide filtered sources only. It works by policy. Look `SKILL.md` for details.

# Documentation Guide

"Documentation" refers to standalone docs, inline comments, and docstrings.

## Principles
1. **Code is the Ground Truth**: Write documentation only to explain non-obvious rationale, behaviors, and constraints that cannot be inferred directly from the code.

## Index & Staleness Management
1. Every agent-managed directory (e.g., `/adr`) must contain `index.md` and `stale.md`.
2. File Naming: `<16-char-hex-id>-<kebab-case-name>.md` (e.g., `3f8a9c12b0e45d67-auth-flow.md`).
3. `index.md` Format: Use the following structure separated by `---` for grep/find compatibility:
   ````
   File: <file-name>
   Summary: <one-line summary>
   Related Files: <comma-separated repo paths>
   Related Symbols: <comma-separated function/class/module names>
   ````
4. `stale.md` Format: append stale files for each line.

## Shared Comment & Docstring Synchronization Rules

Follow these rules when identical docstrings or comments must be maintained across multiple locations:

1.	Generate a synced ID: Generate a 48-bit random hexadecimal ID (12 hex characters, e.g., a1b2c3d4e5f6).
2.	Create the tracking file: Create a file at `synced-comments/<synced_id>.md` with the following structure:

````
---
version: 1
count: <number of associated code locations>
---

# Content
<Write the shared comment or docstring here>

# Version Log
## v1 Log
- Initial creation.
````
3.	Annotate in code: In all associated code locations, include the synchronization tag: `synced id: <synced_id>, version: <n>, count: <n>`
4. Handle content updates: When the shared content changes,
- Increment the version in the frontmatter and update the version: tag across all linked code locations.
- Add a new entry under the # Version Log section.
5.	Version bump trigger: Any code modification in a file that alters or directly affects a block labeled with a synced id triggers a required version bump for that ID.
6. Deprecation & Removal: When removing the shared content entirely.
- Remove all corresponding comments/docstrings from every referenced code location.
- Increment the document's version, record the removal reason in the version log, and add obsolete: true to the frontmatter.

# Task Guide
1. DO NOT arbitrary determine unspecified details of task. Freely talk back to resolve undermined and ambiguous details.
2. Once task specifications are finalized, invoke an isolated sub-agent (fresh session, NO session `fork`) to write and run independent test suites
