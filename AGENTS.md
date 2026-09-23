# AGENTS.md — Pipeline Route Shapefile Generator & Fast QC

## Mission

This repository contains **Pipeline Route Shapefile Generator & Fast QC**, a lightweight, local-first PySide6 desktop application for creating, visualising, inspecting and quality-checking shapefiles.

The application is not intended to become a general-purpose GIS. Its primary qualities are spatial-data correctness, responsive handling of large shapefiles, simple workflows and maintainable code.

## Read first

Before non-trivial work, read:

1. `ARCHITECTURE.md` — canonical architecture, invariants and development roadmap.
2. The latest user-tested application stage.
3. Relevant source modules and tests before editing them.

`STAGE20_ARCHITECTURE_REVIEW.md` is a historical audit record, not a second source of truth. Consult it only when the detailed reasoning behind Stage 20 findings is useful.

## Source of truth

Use this precedence when repository information conflicts:

1. Explicit current user request.
2. Applicable `AGENTS.md` instructions.
3. Executable code, tests and configuration for current behaviour.
4. `ARCHITECTURE.md` for intended architecture, invariants and roadmap.
5. Historical stage notes/reviews for background only.
6. Informal comments and snippets as supporting evidence.

Do not silently reconcile conflicting sources. Identify the conflict and use the appropriate source above.

## Repository rules

### Change scope

- Make small, coherent, behaviour-preserving changes.
- Do not combine architectural refactoring with unrelated user-facing functionality unless explicitly requested.
- Do not perform wholesale rewrites.
- Do not rename, move or reformat unrelated code opportunistically.
- Preserve existing public `QCMapCanvas` methods/signals and layer-state behaviour during the incremental refactoring programme unless the current stage explicitly changes them.

### Versioning

- Start from the latest **user-tested** stage.
- Do not overwrite a previous verified stage.
- Use explicit stage filenames such as `PipelineRouteCreator_stage21.py`.
- Keep earlier stages as rollback/reference points.
- Do not treat an untested generated stage as the new baseline.

### Rendering and threading invariants

- Expensive Shapely/GeoPandas processing must not be moved onto the GUI thread merely for convenience.
- Workers may calculate ordinary Python/NumPy/data results, but must **never create or manipulate Qt/pyqtgraph graphics objects**.
- GUI-thread callbacks create/update graphics from worker results.
- Preserve cached `full`, `medium`, `low` and `very_low` LOD rendering for complex geometry.
- Pan/zoom must select cached representations rather than repeatedly simplify source geometry.
- Keep the deliberate single-worker-thread strategy unless profiling demonstrates a need to change it.

### Layer and asynchronous state

- `loaded_layers` remains the compatibility layer registry during the current refactoring programme.
- Preserve Active Layers semantics: visibility, base colour, QC state, selection and layer identity.
- Preserve generation/version checks so stale worker results cannot modify cleared or replaced layers.
- Treat base graphics, LOD graphics, attribute-style graphics, legends/colourbars, selection highlights and Create Mode previews as separate lifecycle-sensitive graphics.
- Every new transient graphics path must define how it is created, updated, hidden and cleared.

### Data and dependencies

- Keep processing local. Do not introduce uploads, telemetry, remote processing or external services without explicit approval.
- Avoid unnecessary dependencies. Prefer the existing Python, PySide6, GeoPandas, Shapely, pyqtgraph and NumPy stack.
- Preserve spatial data and CRS semantics.
- Do not silently alter source data during visualisation or QC.

### Code quality

- Prefer focused functions and modules with clear responsibilities.
- Prefer pure, GUI-independent functions for calculations that do not require Qt.
- Use type hints where they improve clarity.
- Document non-obvious **why**, not obvious **what**.
- Avoid broad `except Exception` in new data-processing code unless there is a documented reason.
- Do not replace working subsystems merely for architectural elegance.

## Verification requirements

For every implementation stage:

1. Inspect the current implementation and relevant architecture documentation.
2. Define the stage scope and what behaviour must remain unchanged.
3. Make the smallest coherent change.
4. Run syntax/static checks at minimum.
5. Run automated tests for affected pure logic when available.
6. If the environment permits, launch the GUI and exercise the affected workflow with representative shapefiles.
7. If GUI execution is unavailable, say so explicitly; do not claim GUI testing.
8. Report checks performed and known limitations.
9. Produce a clearly named stage file/supporting module.
10. Wait for user confirmation before beginning the next stage.

## Current baseline

The current functional baseline is **Stage 19**, with Stage 20 completed as an architecture/code-quality audit. Stage 20 added no intended user-facing functionality.

The next planned implementation is **Stage 21 — Testable Core**. It should establish GUI-independent tests before deeper architectural extraction.

See `ARCHITECTURE.md` for the complete current feature set and roadmap.

## Do not add to this file

Do not use `AGENTS.md` as:

- a detailed architecture document;
- a changelog or incident diary;
- temporary task notes;
- a copy of the full roadmap;
- a substitute for tests or executable configuration.

When a rule becomes obsolete, update or remove it rather than preserving historical instructions.
