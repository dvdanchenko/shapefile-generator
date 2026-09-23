# Architecture — Pipeline Route Shapefile Generator & Fast QC

## 1. Purpose and architectural direction

**Pipeline Route Shapefile Generator & Fast QC** is a lightweight PySide6 desktop application for:

- creating Point, LineString and Polygon shapefiles from coordinate input;
- interactively creating geometry from map clicks;
- loading and visually QC'ing multiple shapefile layers;
- inspecting features and attributes;
- styling layers by attributes;
- producing HTML and Excel QC reports.

The product direction is a focused **spatial-data creation + inspection + QC tool**, not a general-purpose desktop GIS.

The architecture therefore prioritises:

1. spatial-data correctness;
2. responsive GUI performance, including large shapefiles;
3. simple local workflows;
4. useful QC and inspection information;
5. incremental maintainability;
6. minimal dependency and infrastructure overhead.

## 2. Documentation roles

The repository intentionally separates agent instructions from architectural knowledge.

| Document | Role | Authority |
|---|---|---|
| `AGENTS.md` | Short, durable instructions for coding agents | Repository operating rules |
| `ARCHITECTURE.md` | Current architecture, invariants, lifecycle and roadmap | Canonical architecture reference |
| `STAGE20_ARCHITECTURE_REVIEW.md` | Detailed historical audit supporting the Stage 20 conclusions | Background/decision record |
| Stage source files | Executable implementation and current behaviour | Behavioural evidence |
| Tests | Executable expectations for test-covered logic | Behavioural evidence |

Do not duplicate detailed architecture or historical reasoning into `AGENTS.md`.

## 3. Current implementation baseline

### 3.1 Functional status

- **Stage 19** is the latest functional/release-candidate baseline.
- **Stage 20** is completed as an architecture/code-quality audit.
- **Stage 21** is the next planned implementation stage.
- Stages 1–19 introduced the current user-facing functionality; Stage 20 intentionally did not add a feature.

### 3.2 Current source modules

```text
PipelineRouteCreator_stage19.py
    Main application and QCMapCanvas integration point

pipeline_route_creator_workers.py
    Geometry preparation and background workers

pipeline_route_creator_table.py
    Qt model/view attribute-table models

AGENTS.md
    Agent operating rules

ARCHITECTURE.md
    Canonical architecture and roadmap

STAGE20_ARCHITECTURE_REVIEW.md
    Historical Stage 20 audit/decision record
```

Earlier stage files are retained as rollback/reference points. The latest **user-tested** stage is the implementation baseline for subsequent work.

## 4. Current functional capabilities

### 4.1 Coordinate creation

- Paste Easting/Northing coordinate pairs.
- Select the active CRS.
- Create `Points`, `Line (LineString)` or `Polygon` shapefiles.
- Polygon creation validates minimum distinct points, ring closure, non-zero area and Shapely validity.
- Generated shapefiles are automatically loaded into the QC map.
- Interactive Create Mode collects coordinates by clicking the QC map.
- Create Mode provides live preview and Finish/Cancel behaviour.
- Create Mode is separate from normal feature picking/inspection.
- `Esc` cancels active Create Mode.

### 4.2 Loading and map rendering

- Drag/drop `.shp` files.
- Multiple shapefile layers.
- Multi-file dialog loading.
- Recursive folder loading.
- Case-insensitive `.shp` discovery.
- Batch loading feedback.
- Vectorised point rendering.
- NaN-separated line/polygon rendering.
- Cached `full`, `medium`, `low` and `very_low` representations for complex geometry.
- Background LOD preparation.
- Fit All, Reset View, Zoom In and Zoom Out.
- Throttled cursor coordinate readout.

### 4.3 Layer management

- Active Layers list.
- Visibility checkbox.
- Direct colour-swatch selection.
- Context-menu Change Color.
- QC status indicator.
- Zoom to Layer.
- Individual layer removal.
- Show All / Hide All.
- Persistent window/splitter state.

### 4.4 QC and inspection

- Load-time file/layer statistics.
- Background geometry QC.
- Spatial-index-assisted feature picking.
- Feature highlighting and information display.
- Multiple/overlapping feature candidate selection.
- Attribute table with search/sort.
- HTML and Excel QC report export.
- Project/layer QC status handling.

### 4.5 Attribute-driven styling

- Numeric attributes mapped to a continuous colour scale.
- Configurable numeric range.
- Numeric colourbar.
- Null/non-finite numeric fallback to the base layer colour.
- Categorical attributes mapped to discrete colours.
- Categorical legend.
- Attribute styling independent of the base layer colour.
- Explicit clearing/restoration of style graphics and auxiliary legend/colourbar graphics.

## 5. Module responsibilities

### 5.1 `PipelineRouteCreator_stage19.py`

This is currently the main integration module. It contains both the application window and `QCMapCanvas`.

The main window owns:

- application/window construction;
- coordinate-entry UI;
- CRS selection;
- shapefile creation workflow;
- Active Layers UI;
- menus, toolbars and shortcuts;
- persistent window state;
- top-level workflow coordination.

`QCMapCanvas(pg.PlotWidget)` currently owns several responsibilities that are candidates for future extraction:

- shapefile loading and CRS handling;
- layer registry/lifecycle;
- base rendering;
- LOD orchestration;
- geometry QC coordination;
- spatial-index coordination;
- feature selection/highlighting/inspection;
- attribute-driven styling;
- Create Mode preview;
- attribute-table/report launching;
- map navigation.

This concentration is a known architectural limitation, not a reason for a rewrite.

### 5.2 `pipeline_route_creator_workers.py`

Owns CPU-side geometry preparation and worker execution, including:

- coordinate/NaN-separator helpers;
- GIS render-array construction;
- LOD worker and signals;
- geometry QC worker and signals;
- spatial-index worker and signals.

Workers return ordinary data such as NumPy arrays/results. They do not create or manipulate Qt/pyqtgraph graphics objects.

### 5.3 `pipeline_route_creator_table.py`

Owns the Qt model/view implementation for the attribute table:

- attribute table model;
- filtering/sorting proxy model.

The table must remain model/view based rather than constructing one widget per cell for large layers.

## 6. Layer state model

`loaded_layers` is currently the central compatibility registry.

A layer conceptually contains:

```text
identity/path
geometry / GeoDataFrame when required
base graphics
visibility
base colour
extent
cached LOD graphics + active LOD
load-time statistics
QC state/results
spatial-index state
attribute-style configuration
```

Asynchronous work additionally carries layer identity/generation information so results can be rejected when the corresponding layer is stale.

### Architectural constraint

Do not replace the current dictionary representation in one large conversion. The planned transition is:

```text
current layer dictionaries
        ↓
internal typed LayerState representation
        ↓
progressively reduce direct dictionary coupling
```

The public/observable behaviour of `QCMapCanvas` should remain stable during this transition unless a stage explicitly requires otherwise.

## 7. Rendering architecture

### 7.1 Geometry rendering

Points and MultiPoints use vectorised coordinate extraction and a `ScatterPlotItem`.

LineString/MultiLineString and Polygon/MultiPolygon geometries are flattened into NumPy coordinate arrays. NaN separators divide independent geometries/rings, allowing `PlotCurveItem(connect='finite')` to render many features without creating one graphics item per feature.

### 7.2 LOD

Complex geometry has cached representations:

```text
FULL
  ↓ background preparation
MEDIUM
LOW
VERY_LOW
```

The expensive Shapely simplification work is performed in a background worker. The GUI thread creates/updates pyqtgraph graphics from returned arrays.

The current worker pool deliberately uses one worker thread to limit CPU/RAM contention with the GUI and large datasets.

### 7.3 Navigation invariant

Pan/zoom may choose among existing representations but must not repeatedly simplify the source GeoDataFrame.

This invariant is central to the application's performance behaviour.

## 8. Threading and asynchronous safety

The intended boundary is:

```text
GeoPandas / Shapely data
          |
          +--------------------------+
          |                          |
          v                          v
GUI thread                       Worker thread
Qt/pyqtgraph graphics            CPU/data calculations
creation/update                  LOD / QC / index
          ^                          |
          +------- result -----------+
```

Workers must never instantiate or manipulate Qt graphics objects.

Workers carry layer identity and generation information. When a layer is removed or the canvas is cleared, the generation changes. A returned result is applied only if it still belongs to the current layer/generation.

Generation invalidation is intentionally **not** treated as true cancellation: an already-running worker may finish its current operation, but its stale result must not modify current GUI state.

Cooperative cancellation is a future optimisation only if profiling/stress testing demonstrates a real need.

## 9. Graphics lifecycle model

The application contains multiple independent graphics lifecycles. A future change must identify which lifecycle it affects.

```text
BASE LAYER GRAPHICS
       |
       +--> LOD graphics may replace visible representation during navigation

ATTRIBUTE STYLE
       ↓
hide base graphics
       ↓
create styled graphics
       ↓
optional LegendItem / ColorBarItem

CLEAR ATTRIBUTE STYLE
       ↓
remove styled graphics
       ↓
detach/remove auxiliary graphics
       ↓
restore base graphics

FEATURE SELECTION
       ↓
create highlight
       ↓
replace/clear previous highlight

CREATE MODE
       ↓
create temporary preview graphics
       ↓
finish/save OR cancel/clear

REMOVE/CLEAR LAYER
       ↓
invalidate asynchronous generation
       ↓
remove all associated graphics
       ↓
ignore stale worker results
```

Attribute-style auxiliary objects are especially lifecycle-sensitive because parent-owned pyqtgraph objects such as legends/colourbars may not behave like ordinary plot data items during removal.

## 10. QC architecture

Geometry QC is separate from initial rendering.

Current checks include:

- invalid geometries;
- null geometries;
- empty geometries;
- zero-length linear geometries;
- duplicate geometries;
- geometry-type breakdown;
- missing CRS warning.

QC is performed in background work where appropriate. The UI exposes compact states:

```text
CHECKING...
PASS
WARNING
ERROR
```

Reports should consume the same statistics/QC results used by the UI rather than implementing an independent QC calculation.

## 11. Inspection and selection architecture

Feature picking uses a spatial index to narrow candidates, followed by geometry-aware proximity/intersection checks.

The resulting feature may be:

- highlighted;
- shown in feature information;
- zoomed to;
- inspected through the attribute table.

When several candidates are under the click, the user is presented with a candidate-selection workflow instead of arbitrary selection.

This subsystem is a future extraction target because candidate calculation, highlighting and inspection presentation are currently coupled to `QCMapCanvas`.

## 12. Reporting architecture

The current reporting path obtains information from loaded layer statistics/QC state and exports HTML and Excel.

The intended future separation is:

```text
layer state
    ↓
report data model
    ↓
HTML / Excel renderers
    ↓
file-dialog / UI layer
```

The report must remain a presentation of application QC state, not a second independent QC implementation.

## 13. Stage 20 architecture findings

Stage 20 audited the Stage 19 release-candidate implementation. The audit identified the following priorities.

### HIGH — responsibility concentration

`QCMapCanvas` remains the main coupling point for loading, state, rendering, LOD, styling, selection, creation, inspection, reporting and navigation.

**Direction:** extract cohesive responsibilities incrementally while preserving the existing canvas API.

### HIGH — layer state coupling

The `loaded_layers` dictionaries combine identity, graphics, LOD, QC, spatial-index, selection and styling state.

**Direction:** introduce an internal typed `LayerState` representation without a big-bang conversion.

### HIGH — attribute styling lifecycle

Attribute styling has its own graphics, auxiliary graphics and per-layer style state.

**Direction:** isolate creation, update, clear and visibility synchronisation in a rendering controller/owner.

### HIGH — automated testing gap

The current project has static compilation but lacks a formal automated suite covering the GUI-independent geometry/state logic.

**Direction:** establish pure-function tests before major architectural extraction.

### MEDIUM — selection/inspection coupling

Candidate calculation, highlighting and inspection presentation remain inside the canvas.

**Direction:** extract candidate logic and inspection formatting before expanding selection functionality.

### MEDIUM — reporting coupling

Report data generation and export UI are mixed.

**Direction:** separate data model, renderers and UI export handling.

### MEDIUM — geometry helper size

`_build_gis_render_arrays()` is a large multi-geometry function.

**Direction:** decompose it into focused pure functions while preserving its output contract.

### MEDIUM — broad exception handling

Broad exception handlers are used in several places. Some are appropriate for defensive GUI cleanup, but new data-processing code should distinguish expected failures from programming errors.

### LOW/MEDIUM — worker cancellation

Generation invalidation prevents stale results but does not stop already-running CPU work.

**Direction:** leave as-is unless stress testing demonstrates a practical need for cooperative cancellation.

### LOW — global pyqtgraph configuration

Global pyqtgraph configuration is intentional for this single application and should remain centralised.

## 14. Incremental refactoring roadmap

The roadmap below supersedes the old Stage 13–19 planning section. Stages 1–19 are historical implementation stages; the sequence below is the current plan from Stage 21 onward.

### Stage 21 — Testable Core

**Goal:** establish a GUI-independent test foundation without intentional user-visible change.

Initial targets:

- coordinate parsing;
- polygon validation;
- geometry render arrays and NaN separators;
- LOD level selection;
- layer-statistics calculations;
- attribute classification/binning;
- overlapping-feature candidate ordering.

Rules:

- extract only the smallest pure functions needed;
- avoid redesigning `QCMapCanvas`;
- preserve existing outputs/contracts;
- add automated tests for each extracted function.

**Exit criteria:** tests run reproducibly; existing application still syntax-checks; no intended GUI behaviour change.

### Stage 22 — Layer State Model

**Goal:** reduce direct coupling to `loaded_layers` dictionaries.

Introduce a typed/internal `LayerState` structure for layer metadata while maintaining a compatibility transition.

Expected direction:

```text
LayerState
├── identity
├── source/data
├── display state
├── render state
├── LOD state
├── QC state
├── index state
└── attribute-style state
```

**Exit criteria:** state ownership is clearer without breaking existing canvas consumers.

### Stage 23 — Rendering Controllers

**Goal:** separate base rendering, LOD rendering and attribute rendering.

Potential internal responsibilities:

```text
BaseRenderer
LODRenderer
AttributeStyleRenderer
```

These names are architectural concepts, not mandatory final class names.

**Exit criteria:** rendering lifecycle ownership is explicit; existing visual behaviour remains equivalent.

### Stage 24 — Selection/Inspection Controller

**Goal:** move candidate finding, selection/highlighting and inspection formatting out of the map canvas where practical.

Preserve the Stage 18 multi-candidate behaviour.

**Exit criteria:** selection logic can be tested independently of most map UI code.

### Stage 25 — Reporting Subsystem

**Goal:** separate report data generation from HTML/Excel rendering and UI file selection.

Target:

```text
Layer/QC state → report data → renderer → export UI
```

**Exit criteria:** report calculations are testable without launching the GUI and exported formats continue to represent the same QC information.

### Stage 26 — Geometry Worker Decomposition

**Goal:** split the large geometry rendering helper into focused pure functions by geometry type and common handling.

Potential boundaries:

- points;
- lines;
- polygons/rings;
- multi-geometries;
- empty/unsupported handling;
- shared coordinate/separator handling.

**Exit criteria:** render-array output remains compatible with existing rendering and tests cover each geometry family.

### Stage 27+ — Product-driven enhancements

After the architectural foundation is stable, return to user-facing features based on actual product needs rather than assuming a fixed feature sequence.

Possible future areas include:

- richer attribute exploration/filtering;
- advanced attribute styling;
- map measurement/navigation tools;
- layer ordering/grouping;
- expanded geometry editing;
- additional spatial QC checks;
- additional vector formats;
- other data viewers if the product direction expands.

These are candidates, not commitments.

## 15. Testing strategy

The testing pyramid should favour cheap, deterministic tests:

```text
                 GUI/manual tests
                       ▲
                       |
              targeted Qt tests
                       ▲
                       |
          pure Python/NumPy/Shapely tests
                       ▲
                       |
              syntax/static checks
```

Pure tests should cover calculations that do not require Qt. GUI tests should be added selectively for behaviour that cannot be meaningfully tested otherwise.

Representative data should include:

- points;
- lines and multi-lines;
- polygons with holes;
- multi-polygons;
- empty/null geometries;
- invalid polygons;
- duplicated geometries;
- numeric and categorical attributes;
- overlapping features;
- large geometries where performance matters.

## 16. Architectural invariants

The following are non-negotiable unless a deliberate architecture stage explicitly changes them:

1. Workers do not create/manipulate Qt graphics.
2. Expensive geometry processing does not move to the GUI thread.
3. Pan/zoom does not repeatedly simplify source geometry.
4. Cached LOD remains reusable during navigation.
5. Stale asynchronous results cannot modify current layer state.
6. Active Layers visibility/colour/status/identity semantics remain stable.
7. Attribute styling cannot accidentally expose the underlying base layer as a second global-colour overlay.
8. Attribute-style legends/colourbars must be explicitly cleaned up.
9. Create Mode previews must not leak into normal QC mode.
10. Feature selection must not arbitrarily discard overlapping candidates.
11. QC reports consume the application's QC/statistics state rather than silently implementing a different QC algorithm.
12. Spatial data and CRS semantics are preserved.
13. Local-first operation remains the default.

## 17. What not to do

Avoid:

- wholesale rewrites;
- turning the application into a general-purpose GIS without an explicit product decision;
- replacing pyqtgraph/GeoPandas/Shapely solely for architectural fashion;
- moving expensive processing to the GUI thread;
- per-feature graphics for large layers when vectorised rendering is sufficient;
- dependency-heavy state-management frameworks without demonstrated need;
- refactoring several unrelated subsystems in one stage;
- duplicating architecture or roadmap content in `AGENTS.md`;
- treating historical stage reports as current implementation truth;
- adding user-facing features to a refactoring stage unless explicitly requested.

## 18. Historical stage summary

Stages 1–12 established the performance and QC foundation: multi-geometry rendering, cached LOD, navigation, layer information, geometry QC, feature inspection, attribute tables, reporting, workflow polish and performance improvements.

Stage 13 introduced the first conservative module split.

Stage 14 added polygon creation from pasted coordinates.

Stage 15 added interactive Create Mode.

Stage 16 added direct Active Layers colour selection.

Stage 17 added numeric/categorical attribute styling and its lifecycle cleanup.

Stage 18 added overlapping/multi-feature selection.

Stage 19 integrated final UX/QC/release-hardening work and is the current functional baseline.

Stage 20 audited the architecture and established the refactoring direction documented above.

For detailed Stage 20 reasoning, see `STAGE20_ARCHITECTURE_REVIEW.md`.
