# Stage 20 — Architecture & Code Quality Audit

## Scope

Stage 20 reviews the Stage 19 release-candidate codebase without adding user-facing functionality. The goal is to identify technical debt, state-management risks, performance risks and the safest order for future refactoring.

Reviewed files:

- `PipelineRouteCreator_stage19.py` — 3,107 lines
- `pipeline_route_creator_workers.py` — 532 lines
- `pipeline_route_creator_table.py` — 89 lines

Static syntax compilation passes for all three files.

## Executive summary

The Stage 19 architecture is fundamentally sound for the current application. The most important design decisions should be preserved: cached LOD rendering, background CPU work, GUI-thread-only graphics creation, generation checks for asynchronous results, a central layer registry, and Qt model/view for attribute tables.

The main remaining issue is **responsibility concentration**. `QCMapCanvas` still owns loading, layer state, rendering, LOD orchestration, attribute styling, selection, creation preview, inspection, reporting and navigation. `PipelineRouteCreator` also mixes UI construction, file loading, layer-list behaviour and shapefile creation.

No large rewrite is recommended. Future refactoring should be incremental and behaviour-preserving.

## Findings

### 1. QCMapCanvas remains too large — HIGH priority

`QCMapCanvas` spans roughly 2,200 lines and contains several independent subsystems.

The largest methods include:

- `add_shapefile_layer()` — 186 lines
- `_render_attribute_style()` — 132 lines
- `_choose_feature_candidate()` — 117 lines
- `export_qc_report()` — 87 lines
- `show_attribute_style_dialog()` — 89 lines
- `_build_layer_statistics()` — 57 lines
- `show_attribute_table()` — 69 lines

This makes regressions more likely when one feature modifies shared state.

**Recommendation:** extract cohesive helpers first, without changing the public `QCMapCanvas` API.

### 2. Layer state is the main coupling point — HIGH priority

`loaded_layers` is doing the right job as the central registry, but the dictionaries contain state for rendering, LOD, QC, spatial indexing, selection and attribute styling.

This is workable now but increasingly difficult to reason about as features are added.

**Recommendation:** introduce a small internal `LayerState` data structure in a future refactoring stage. Keep the existing dictionary-compatible access during the transition if necessary.

### 3. Attribute styling has become a separate rendering subsystem — HIGH priority

Attribute styling currently maintains separate graphics collections and state:

- styled graphics
- auxiliary graphics
- styled layer ID
- field
- mode
- per-layer style configuration

The previous Stage 17 bugs demonstrated that this state has a lifecycle different from ordinary layer graphics.

**Recommendation:** isolate attribute styling into an internal renderer/controller. Its only responsibilities should be create, update, clear and visibility synchronisation.

### 4. Feature picking and overlapping-feature UI are tightly coupled — MEDIUM priority

`_pick_features()`, `_choose_feature_candidate()`, `_highlight_feature()` and `show_feature_information()` form a coherent feature-selection subsystem, but they currently live inside the map canvas.

**Recommendation:** extract candidate calculation and feature-inspection formatting before adding more selection functionality.

### 5. Report generation is too tightly coupled to the canvas — MEDIUM priority

HTML and Excel reporting currently obtain data directly from map/layer state and also contain UI/export handling.

**Recommendation:** separate:

```text
layer state → report data model → HTML/Excel renderer → file dialog
```

This will make reporting testable without launching Qt.

### 6. Geometry rendering helper is large — MEDIUM priority

`_build_gis_render_arrays()` in the worker module is approximately 265 lines. It handles multiple geometry types and their coordinate/NaN-separator rules.

This is a good candidate for decomposition into geometry-type-specific pure functions:

- points
- lines
- polygons
- multi-geometries
- unsupported/empty handling

The existing output contract should remain unchanged.

### 7. Exception handling is broad in several places — MEDIUM priority

The main module contains many `except Exception` blocks. Some are appropriate for defensive GUI cleanup, but broad exception handling around data processing can hide real programming errors.

**Recommendation:** distinguish between:

- expected optional cleanup failures;
- known library/data exceptions;
- unexpected programming errors.

Do this gradually; do not replace every broad exception handler at once.

### 8. Testing is the biggest missing engineering layer — HIGH priority

Static compilation is currently available, but the project does not yet have a formal automated test suite covering the non-GUI geometry and state logic.

This is now the main prerequisite for safe architectural changes.

**Recommendation:** add pure-function tests before major refactoring. Initial targets:

- geometry rendering arrays;
- polygon validation;
- coordinate parsing;
- LOD level selection;
- statistics calculations;
- attribute classification/binning;
- overlapping-feature candidate ordering.

GUI tests can follow later.

### 9. Worker cancellation is generation-based rather than true cancellation — LOW/MEDIUM priority

The current architecture safely ignores stale worker results using generation checks. That is correct and should be retained.

However, a worker already executing expensive Shapely operations may continue until completion after a layer is cleared.

**Recommendation:** do not complicate this immediately. If stress testing shows shutdown/clear delays, add cooperative cancellation at natural geometry-processing boundaries.

### 10. Global pyqtgraph configuration is intentional but should remain centralised — LOW priority

The application configures pyqtgraph globally for background, foreground, antialiasing and OpenGL.

This is acceptable for a single-application process. Keep it in one place and do not scatter rendering configuration throughout the codebase.

## State/lifecycle model

The most important state transitions should be documented as:

```text
LOAD
  ↓
REGISTER LAYER
  ↓
RENDER BASE GRAPHICS
  ↓
START BACKGROUND WORK
  ├── LOD
  ├── GEOMETRY QC
  └── SPATIAL INDEX

STYLE BY ATTRIBUTE
  ↓
HIDE BASE GRAPHICS
  ↓
CREATE ATTRIBUTE GRAPHICS
  ↓
OPTIONAL LEGEND / COLORBAR

CLEAR ATTRIBUTE STYLE
  ↓
REMOVE STYLE GRAPHICS + AUXILIARY ITEMS
  ↓
RESTORE BASE GRAPHICS

REMOVE/CLEAR LAYER
  ↓
INVALIDATE GENERATION
  ↓
REMOVE ALL GRAPHICS
  ↓
DISCARD STALE WORKER RESULTS
```

This lifecycle is now important enough that future changes should explicitly state which transition they modify.

## Recommended refactoring order

### Stage 21 — Testable core

Extract pure, GUI-independent functions and create a small automated test suite.

No user-visible functionality change.

### Stage 22 — Layer state model

Introduce a typed/internal layer-state representation while preserving current behaviour.

### Stage 23 — Rendering controllers

Separate base rendering, LOD rendering and attribute rendering responsibilities.

### Stage 24 — Selection/inspection controller

Separate spatial candidate finding, highlighting and inspection presentation.

### Stage 25 — Reporting subsystem

Separate report data generation from HTML/Excel export UI.

### Stage 26 — Geometry worker decomposition

Break `_build_gis_render_arrays()` into focused pure geometry functions.

### Stage 27 onward

Return to user-facing enhancements only after the above foundations are stable.

## Explicitly avoid

- A complete rewrite of the application.
- Introducing a GIS framework solely for architectural reasons.
- Moving Shapely/GeoPandas work back onto the GUI thread.
- Replacing cached LOD rendering with per-feature graphics.
- Introducing a dependency-heavy state-management framework.
- Refactoring multiple subsystems simultaneously.

## Stage 20 conclusion

Stage 19 does not require a rewrite. The application has reached the point where **testability and separation of responsibilities** are more valuable than immediately adding another large feature.

The safest next implementation is therefore **Stage 21: Testable Core**, starting with pure geometry/LOD/attribute helper functions and automated tests while keeping the current GUI behaviour unchanged.
