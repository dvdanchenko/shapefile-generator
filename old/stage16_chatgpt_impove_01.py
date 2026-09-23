"""
Stage	Focus	Main additions
3	✅ Rendering performance	Cached multi-resolution rendering, responsive pan/zoom
4	✅ Navigation	Fit All, Zoom to Layer, zoom controls, coordinate readout
5	Layer intelligence	Feature/layer statistics, better layer context menu
6	Geometry QC	Invalid geometry, empty geometry, duplicates, zero-length features, etc.
7	Feature inspection	Click feature → highlight + attributes
8	Attribute table	Sort/filter attributes, double-click → zoom to feature
9	QC reporting	HTML + CSV/Excel QC reports
10	Workflow polish	Folder loading, progress indicators, settings, shortcuts, UX cleanup
"""


import sys
import time
from pathlib import Path
import numpy as np
import geopandas as gpd
import shapely
from shapely.geometry import Point, LineString, Polygon
import pyqtgraph as pg
from pyproj import CRS

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPlainTextEdit, QComboBox, QPushButton, QFileDialog, QLabel,
    QSplitter, QListWidget, QListWidgetItem, QMessageBox,
    QColorDialog, QMenu, QToolBar
)
from PySide6.QtCore import Qt, Signal, QObject, QRunnable, QThreadPool, QTimer
from PySide6.QtGui import QPixmap, QColor, QIcon, QAction

# Global pyqtgraph settings
pg.setConfigOption('background', '#ffffff')
pg.setConfigOption('foreground', '#202020')
pg.setConfigOptions(antialias=True, useOpenGL=True)  # OpenGL needs PyOpenGL installed


def bouquet_studio_stylesheet() -> str:
    return """
        QMainWindow { background-color: #f2f2f2; }
        QWidget { color: #202020; font-family: Segoe UI, sans-serif; }
        QPlainTextEdit { background-color: #ffffff; border: 1px solid #777777; padding: 5px; }
        QPushButton { background-color: #ffffff; border: 1px solid #777777; padding: 6px; border-radius: 3px; }
        QPushButton:hover { border: 1px solid cyan; background-color: #f8f8f8; }
        QComboBox { background-color: #ffffff; border: 1px solid #777777; padding: 4px; }
        QListWidget { background-color: #ffffff; border: 1px solid #777777; padding: 3px; }
        QListWidget::item { padding: 4px; }
        QListWidget::item:hover { background-color: #f0f0f0; }
        QStatusBar { background-color: #e0e0e0; color: #202020; padding-left: 5px; border-top: 1px solid #777777; }
        QMessageBox { background-color: #f2f2f2; }
        QMenu { background-color: #ffffff; border: 1px solid #777777; }
        QMenu::item:selected { background-color: #e0e0e0; }
    """


def make_color_icon(color_hex: str) -> QIcon:
    pixmap = QPixmap(12, 12)
    pixmap.fill(QColor(color_hex))
    return QIcon(pixmap)


def _coords_with_nan_breaks(coords: np.ndarray, index: np.ndarray):
    """
    Flatten coordinates while inserting NaN separators between geometries.

    NaNs are intentional here because PlotCurveItem with connect='finite'
    treats them as breaks. Do NOT use skipFiniteCheck=True with this data.
    """
    if coords.size == 0:
        return (
            np.empty(0, dtype=np.float64),
            np.empty(0, dtype=np.float64),
        )

    coords = np.asarray(coords, dtype=np.float64)
    index = np.asarray(index)

    # A geometry break occurs whenever the geometry index changes.
    break_positions = np.flatnonzero(np.diff(index)) + 1
    n_breaks = break_positions.size

    if n_breaks == 0:
        return coords[:, 0].copy(), coords[:, 1].copy()

    out_n = coords.shape[0] + n_breaks
    x = np.empty(out_n, dtype=np.float64)
    y = np.empty(out_n, dtype=np.float64)

    src_start = 0
    dst_start = 0

    for src_end in np.r_[break_positions, coords.shape[0]]:
        n = src_end - src_start
        x[dst_start:dst_start + n] = coords[src_start:src_end, 0]
        y[dst_start:dst_start + n] = coords[src_start:src_end, 1]
        dst_start += n

        if src_end != coords.shape[0]:
            x[dst_start] = np.nan
            y[dst_start] = np.nan
            dst_start += 1

        src_start = src_end

    return x, y


class _LODWorkerSignals(QObject):
    finished = Signal(str, int, object)
    failed = Signal(str, int, str)


class _LODWorker(QRunnable):
    """
    Background worker for expensive Shapely simplification/coordinate
    extraction.

    IMPORTANT: no Qt graphics items are created here. Only NumPy arrays are
    produced; PlotCurveItems are created in the GUI thread.
    """

    def __init__(self, layer_id, generation, gdf, tolerances):
        super().__init__()
        self.layer_id = layer_id
        self.generation = generation
        self.gdf = gdf
        self.tolerances = tolerances
        self.signals = _LODWorkerSignals()
        self.setAutoDelete(True)

    def run(self):
        try:
            result = {}

            for level, tolerance in self.tolerances.items():
                x, y = _build_gis_render_arrays(
                    self.gdf,
                    tolerance=tolerance,
                )
                result[level] = (x, y)

            self.signals.finished.emit(
                self.layer_id,
                self.generation,
                result,
            )

        except Exception as exc:
            self.signals.failed.emit(
                self.layer_id,
                self.generation,
                str(exc),
            )


def _build_gis_render_arrays(gdf, tolerance: float = 0.0):
    """
    Convert line/polygon geometries to NaN-separated NumPy arrays.

    The zero-tolerance path is vectorized and is used for the immediate
    full-resolution display. Simplified LODs use the same representation
    after one background Shapely simplification pass.
    """
    if tolerance <= 0:
        line_mask = gdf.geometry.geom_type.isin(
            ['LineString', 'MultiLineString']
        )
        poly_mask = gdf.geometry.geom_type.isin(
            ['Polygon', 'MultiPolygon']
        )

        x_parts = []
        y_parts = []

        if line_mask.any():
            lines = (
                gdf.loc[line_mask, 'geometry']
                .explode(index_parts=False)
                .dropna()
            )

            if not lines.empty:
                coords, idx = shapely.get_coordinates(
                    lines.to_numpy(),
                    return_index=True,
                )
                lx, ly = _coords_with_nan_breaks(
                    coords,
                    idx,
                )

                if lx.size:
                    x_parts.append(lx)
                    y_parts.append(ly)

        if poly_mask.any():
            polys = (
                gdf.loc[poly_mask, 'geometry']
                .explode(index_parts=False)
                .dropna()
            )

            if not polys.empty:
                rings = shapely.get_exterior_ring(
                    polys.to_numpy()
                )

                coords, idx = shapely.get_coordinates(
                    rings,
                    return_index=True,
                )
                px, py = _coords_with_nan_breaks(
                    coords,
                    idx,
                )

                if px.size:
                    x_parts.append(px)
                    y_parts.append(py)

                # Preserve polygon holes.
                interiors = []

                for polygon in polys.to_numpy():
                    if polygon is not None and not polygon.is_empty:
                        interiors.extend(
                            list(polygon.interiors)
                        )

                if interiors:
                    coords, idx = shapely.get_coordinates(
                        np.asarray(
                            interiors,
                            dtype=object,
                        ),
                        return_index=True,
                    )

                    hx, hy = _coords_with_nan_breaks(
                        coords,
                        idx,
                    )

                    if hx.size:
                        x_parts.append(hx)
                        y_parts.append(hy)

        if not x_parts:
            return (
                np.empty(0, dtype=np.float64),
                np.empty(0, dtype=np.float64),
            )

        arrays_x = []
        arrays_y = []

        for n, (xp, yp) in enumerate(
            zip(x_parts, y_parts)
        ):
            if n:
                arrays_x.append(
                    np.array([np.nan])
                )
                arrays_y.append(
                    np.array([np.nan])
                )

            arrays_x.append(xp)
            arrays_y.append(yp)

        return (
            np.concatenate(arrays_x),
            np.concatenate(arrays_y),
        )

    # Background LOD path. Simplify once, then use vectorized GeoSeries
    # operations to flatten Multi* geometries.
    geometries = gdf.geometry.to_numpy(
        dtype=object
    )

    try:
        geometries = shapely.simplify(
            geometries,
            tolerance,
            preserve_topology=True,
        )
    except Exception:
        geometries = np.asarray(
            [
                (
                    geom.simplify(
                        tolerance,
                        preserve_topology=True,
                    )
                    if geom is not None
                    and not geom.is_empty
                    else geom
                )
                for geom in geometries
            ],
            dtype=object,
        )

    simplified = gpd.GeoSeries(
        geometries,
        crs=gdf.crs,
    )

    line_mask = simplified.geom_type.isin(
        ['LineString', 'MultiLineString']
    )
    poly_mask = simplified.geom_type.isin(
        ['Polygon', 'MultiPolygon']
    )

    x_parts = []
    y_parts = []

    if line_mask.any():
        lines = (
            simplified[line_mask]
            .explode(index_parts=False)
            .dropna()
        )

        if not lines.empty:
            coords, idx = shapely.get_coordinates(
                lines.to_numpy(),
                return_index=True,
            )

            lx, ly = _coords_with_nan_breaks(
                coords,
                idx,
            )

            if lx.size:
                x_parts.append(lx)
                y_parts.append(ly)

    if poly_mask.any():
        polys = (
            simplified[poly_mask]
            .explode(index_parts=False)
            .dropna()
        )

        if not polys.empty:
            rings = shapely.get_exterior_ring(
                polys.to_numpy()
            )

            coords, idx = shapely.get_coordinates(
                rings,
                return_index=True,
            )

            px, py = _coords_with_nan_breaks(
                coords,
                idx,
            )

            if px.size:
                x_parts.append(px)
                y_parts.append(py)

            interiors = []

            for polygon in polys.to_numpy():
                if polygon is not None and not polygon.is_empty:
                    interiors.extend(
                        list(polygon.interiors)
                    )

            if interiors:
                coords, idx = shapely.get_coordinates(
                    np.asarray(
                        interiors,
                        dtype=object,
                    ),
                    return_index=True,
                )

                hx, hy = _coords_with_nan_breaks(
                    coords,
                    idx,
                )

                if hx.size:
                    x_parts.append(hx)
                    y_parts.append(hy)

    if not x_parts:
        return (
            np.empty(0, dtype=np.float64),
            np.empty(0, dtype=np.float64),
        )

    arrays_x = []
    arrays_y = []

    for n, (xp, yp) in enumerate(
        zip(x_parts, y_parts)
    ):
        if n:
            arrays_x.append(
                np.array([np.nan])
            )
            arrays_y.append(
                np.array([np.nan])
            )

        arrays_x.append(xp)
        arrays_y.append(yp)

    return (
        np.concatenate(arrays_x),
        np.concatenate(arrays_y),
    )


class _LODWorkerSignals(QObject):
    finished = Signal(str, int, object)
    failed = Signal(str, int, str)


class _LODWorker(QRunnable):
    """
    Background worker for expensive Shapely simplification/coordinate
    extraction.

    IMPORTANT: no Qt graphics items are created here. Only NumPy arrays are
    produced; PlotCurveItems are created in the GUI thread.
    """

    def __init__(self, layer_id, generation, gdf, tolerances):
        super().__init__()
        self.layer_id = layer_id
        self.generation = generation
        self.gdf = gdf
        self.tolerances = tolerances
        self.signals = _LODWorkerSignals()
        self.setAutoDelete(True)

    def run(self):
        try:
            result = {}

            for level, tolerance in self.tolerances.items():
                x, y = _build_gis_render_arrays(
                    self.gdf,
                    tolerance=tolerance,
                )
                result[level] = (x, y)

            self.signals.finished.emit(
                self.layer_id,
                self.generation,
                result,
            )

        except Exception as exc:
            self.signals.failed.emit(
                self.layer_id,
                self.generation,
                str(exc),
            )


class QCMapCanvas(pg.PlotWidget):
    status_message = Signal(str)
    layer_added = Signal(str, str, str)  # (layer_id, display_name, color_hex)
    layers_cleared = Signal()

    PALETTE = ['#00cccc', '#e6007e', '#d9a100', '#2e7d32', '#6a1b9a', '#d84315']

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.showGrid(x=True, y=True, alpha=0.3)
        self.setAspectLocked(True)

        self.getAxis('bottom').setStyle(tickTextOffset=8)
        self.getAxis('left').setStyle(tickTextOffset=8)

        self.loaded_layers = {}  # layer_id -> layer metadata
        self.color_index = 0

        # Stage 3: cached multi-resolution rendering.
        # Geometry simplification happens in a worker, never during pan/zoom.
        self._lod_pool = QThreadPool(self)
        self._lod_pool.setMaxThreadCount(1)

        self._lod_switch_timer = QTimer(self)
        self._lod_switch_timer.setSingleShot(True)
        self._lod_switch_timer.timeout.connect(self._switch_visible_lod)

        self._lod_generation = 0

        self.getViewBox().sigRangeChanged.connect(
            self._schedule_lod_switch
        )

        # Stage 4: navigation and cursor coordinate readout.
        self.scene().sigMouseMoved.connect(self._mouse_moved)

    # ------------------------------------------------------------------
    # Stage 3: cached multi-resolution rendering
    # ------------------------------------------------------------------

    def _estimate_lod_tolerances(self, gdf):
        """
        Estimate three simplification distances from the layer extent.

        The exact values are deliberately data-scale based rather than
        hard-coded metre/degree values, so they work with projected and
        geographic CRSs.
        """
        try:
            min_x, min_y, max_x, max_y = gdf.total_bounds

            span = max(
                abs(float(max_x) - float(min_x)),
                abs(float(max_y) - float(min_y)),
            )

            if not np.isfinite(span) or span <= 0:
                span = 1.0

            # Full is already rendered. These are progressively lighter
            # cached representations.
            base = span / max(
                2000.0,
                float(max(
                    self.viewport().width(),
                    self.viewport().height(),
                )) * 2.0,
            )

            base = max(base, 1e-12)

            return {
                "medium": base * 2.0,
                "low": base * 8.0,
                "very_low": base * 32.0,
            }

        except Exception:
            return {
                "medium": 1.0,
                "low": 4.0,
                "very_low": 16.0,
            }

    def _start_lod_build(self, layer_id):
        layer = self.loaded_layers.get(layer_id)

        if not layer or not layer.get("has_complex_geometry"):
            return

        if layer.get("lod_build_started"):
            return

        gdf = layer.get("gdf")

        if gdf is None or gdf.empty:
            return

        layer["lod_build_started"] = True
        generation = self._lod_generation

        worker = _LODWorker(
            layer_id,
            generation,
            gdf,
            self._estimate_lod_tolerances(gdf),
        )

        worker.signals.finished.connect(
            self._lod_cache_ready
        )
        worker.signals.failed.connect(
            self._lod_cache_failed
        )

        layer["lod_worker"] = worker
        self._lod_pool.start(worker)

        self.status_message.emit(
            f"Preparing fast zoom cache for {layer['name']}..."
        )

    def _create_curve_item(self, x, y, color):
        if x is None or x.size == 0:
            return None

        curve = pg.PlotCurveItem(
            x,
            y,
            pen=pg.mkPen(
                color=color,
                width=1,
            ),
            connect="finite",
            antialias=False,
            skipFiniteCheck=False,
        )

        self.addItem(curve)
        return curve

    def _lod_cache_ready(self, layer_id, generation, arrays):
        if generation != self._lod_generation:
            return

        layer = self.loaded_layers.get(layer_id)

        if layer is None:
            return

        color = layer["color"]
        lod_items = layer.setdefault("lod_items", {})

        for level in ("medium", "low", "very_low"):
            data = arrays.get(level)

            if data is None:
                continue

            x, y = data
            item = self._create_curve_item(
                x,
                y,
                color,
            )

            if item is not None:
                item.setVisible(False)
                lod_items[level] = item

        layer["lod_cache_ready"] = True
        layer["lod_worker"] = None

        self._switch_visible_lod()

        self.status_message.emit(
            f"Fast zoom cache ready: {layer['name']}."
        )

    def _lod_cache_failed(self, layer_id, generation, message):
        if generation != self._lod_generation:
            return

        layer = self.loaded_layers.get(layer_id)

        if layer is not None:
            layer["lod_worker"] = None
            layer["lod_build_started"] = False

            self.status_message.emit(
                f"Fast zoom cache failed for "
                f"{layer['name']}: {message}"
            )

    def _current_lod_level(self, layer):
        """
        Cheap LOD selection.

        This is the only work performed on pan/zoom. No Shapely calls,
        coordinate extraction, or PlotCurveItem creation happen here.
        """
        if not layer.get("has_complex_geometry"):
            return "full"

        try:
            x_range, y_range = self.viewRange()

            view_span = max(
                abs(float(x_range[1]) - float(x_range[0])),
                abs(float(y_range[1]) - float(y_range[0])),
            )

            extent = layer.get("extent_span", 0.0)

            if extent <= 0 or view_span <= 0:
                return "full"

            ratio = view_span / extent

            # Close view -> full resolution.
            # Further away -> increasingly simplified cached data.
            if ratio <= 0.18:
                return "full"
            if ratio <= 0.55:
                return "medium"
            if ratio <= 1.50:
                return "low"
            return "very_low"

        except Exception:
            return "full"

    def _schedule_lod_switch(self, *args):
        if not self.loaded_layers:
            return

        # Debounce only the cheap visibility operation. There is no expensive
        # geometry work behind this timer.
        self._lod_switch_timer.start(30)

    def _switch_visible_lod(self):
        for layer in self.loaded_layers.values():

            if not layer.get("has_complex_geometry"):
                continue

            lod_items = layer.get("lod_items", {})

            desired = self._current_lod_level(layer)

            # Full-resolution item is the original item created at load time.
            candidates = {
                "full": layer.get("geometry_item"),
                "medium": lod_items.get("medium"),
                "low": lod_items.get("low"),
                "very_low": lod_items.get("very_low"),
            }

            selected = candidates.get(desired)

            # If the requested cache has not arrived yet, choose the best
            # available representation without rebuilding anything.
            if selected is None:
                for fallback in (
                    "full",
                    "medium",
                    "low",
                    "very_low",
                ):
                    if candidates.get(fallback) is not None:
                        selected = candidates[fallback]
                        break

            for item in candidates.values():
                if item is not None:
                    item.setVisible(
                        item is selected
                        and layer.get("visible", True)
                    )

            layer["active_lod"] = desired

    def _remove_layer_graphics(self, layer):
        items = set()

        for item in layer.get("items", []):
            if item is not None:
                items.add(item)

        geometry_item = layer.get("geometry_item")
        if geometry_item is not None:
            items.add(geometry_item)

        for item in layer.get("lod_items", {}).values():
            if item is not None:
                items.add(item)

        for item in items:
            try:
                self.removeItem(item)
            except Exception:
                pass

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        shp_files = [url.toLocalFile() for url in urls if url.toLocalFile().lower().endswith('.shp')]

        if not shp_files:
            self.status_message.emit("No valid .shp files found in drop payload.")
            return

        loaded_count = 0
        for filepath in shp_files:
            if self.add_shapefile_layer(filepath):
                loaded_count += 1

        self.status_message.emit(f"Added {loaded_count} layer(s). Active layers: {len(self.loaded_layers)}.")

    def add_shapefile_layer(self, filepath: str, target_crs: str = None) -> bool:
        path_obj = Path(filepath)
        layer_id = str(path_obj.resolve())

        if layer_id in self.loaded_layers:
            self.status_message.emit(f"Layer '{path_obj.name}' is already loaded.")
            return False

        if target_crs is None and hasattr(self.window(), 'get_active_crs'):
            target_crs = self.window().get_active_crs()

        try:
            load_start = time.perf_counter()

            # pyogrio backend is significantly faster than fiona for large files.
            # Falls back automatically if pyogrio isn't installed.
            try:
                gdf = gpd.read_file(filepath, engine="pyogrio")
            except Exception:
                gdf = gpd.read_file(filepath)

            if gdf.empty:
                return False

            # --- CRS Validation & Conversion Prompt ---
            if target_crs:
                if gdf.crs is not None:
                    file_crs_obj = CRS.from_user_input(gdf.crs)
                    target_crs_obj = CRS.from_user_input(target_crs)

                    if file_crs_obj != target_crs_obj:
                        reply = QMessageBox.question(
                            self,
                            "CRS Mismatch Detected",
                            f"Layer '{path_obj.name}' CRS is:\n  • {file_crs_obj.name}\n\n"
                            f"Active Application CRS is:\n  • {target_crs_obj.name} ({target_crs})\n\n"
                            f"Would you like to reproject this layer to match the active system?",
                            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                            QMessageBox.Yes
                        )

                        if reply == QMessageBox.Yes:
                            gdf = gdf.to_crs(target_crs)
                            self.status_message.emit(f"Reprojected {path_obj.name} to {target_crs}.")
                        elif reply == QMessageBox.Cancel:
                            self.status_message.emit(f"Cancelled loading {path_obj.name}.")
                            return False
                else:
                    reply = QMessageBox.warning(
                        self,
                        "Missing CRS (.prj file)",
                        f"The file '{path_obj.name}' has no projection metadata (.prj file missing).\n\n"
                        f"Assume active CRS ({target_crs})?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.Yes
                    )
                    if reply == QMessageBox.Yes:
                        gdf.set_crs(target_crs, inplace=True)
                    else:
                        return False

            color = self.PALETTE[self.color_index % len(self.PALETTE)]
            self.color_index += 1

            graphic_items = []

            # 1. VECTORIZED POINT RENDERING
            point_mask = gdf.geometry.type.isin(['Point', 'MultiPoint'])
            if point_mask.any():
                pts = gdf.geometry[point_mask].explode(index_parts=False)
                coords = shapely.get_coordinates(pts.values)

                scatter = pg.ScatterPlotItem(
                    x=coords[:, 0],
                    y=coords[:, 1],
                    size=6,
                    pen=pg.mkPen(color=color, width=1),
                    brush=pg.mkBrush(color=color),
                    name=path_obj.name
                )
                self.addItem(scatter)
                graphic_items.append(scatter)

            # 2. LINE & POLYGON RENDERING
            #
            # Stage 3 keeps the original full-resolution item for immediate
            # display. Additional resolutions are generated once in the
            # background and cached as separate PlotCurveItems.
            line_mask = gdf.geometry.geom_type.isin(
                ['LineString', 'MultiLineString']
            )
            poly_mask = gdf.geometry.geom_type.isin(
                ['Polygon', 'MultiPolygon']
            )

            has_complex_geometry = bool(
                line_mask.any() or poly_mask.any()
            )

            geometry_item = None

            if has_complex_geometry:
                all_x, all_y = _build_gis_render_arrays(
                    gdf,
                    tolerance=0.0,
                )

                if all_x.size:
                    geometry_item = pg.PlotCurveItem(
                        all_x,
                        all_y,
                        pen=pg.mkPen(
                            color=color,
                            width=1,
                        ),
                        connect='finite',
                        antialias=False,
                        skipFiniteCheck=False,
                    )

                    self.addItem(geometry_item)
                    graphic_items.append(geometry_item)

            try:
                bounds = gdf.total_bounds
                extent_span = max(
                    abs(float(bounds[2]) - float(bounds[0])),
                    abs(float(bounds[3]) - float(bounds[1])),
                )
            except Exception:
                extent_span = 0.0

            self.loaded_layers[layer_id] = {
                'name': path_obj.name,
                'items': graphic_items,
                'geometry_item': geometry_item,
                'gdf': gdf if has_complex_geometry else None,
                'has_complex_geometry': has_complex_geometry,
                'visible': True,
                'color': color,
                'extent_span': extent_span,
                'lod_items': {},
                'lod_cache_ready': False,
                'lod_build_started': False,
                'lod_worker': None,
                'active_lod': 'full',
            }


            elapsed = time.perf_counter() - load_start
            vertex_count = 0
            try:
                vertex_count = sum(
                    len(shapely.get_coordinates(geom))
                    for geom in gdf.geometry
                    if geom is not None and not geom.is_empty
                )
            except Exception:
                pass

            self.status_message.emit(
                f"Loaded {path_obj.name}: "
                f"{len(gdf):,} feature(s), "
                f"{vertex_count:,} vertex(es), "
                f"{elapsed:.2f}s."
            )

            self.layer_added.emit(layer_id, path_obj.name, color)

            if has_complex_geometry:
                # The Active Layers entry is created first. The expensive
                # simplified representations are then prepared off the GUI
                # thread.
                self._start_lod_build(layer_id)

            return True

        except Exception as e:
            self.status_message.emit(f"Error loading {path_obj.name}: {str(e)}")
            return False

    def change_layer_color(self, layer_id: str, new_color: str):
        layer = self.loaded_layers.get(layer_id)

        if layer is None:
            return

        layer["color"] = new_color

        items = list(layer.get("items", []))
        items.extend(
            item
            for item in layer.get("lod_items", {}).values()
            if item is not None
        )

        geometry_item = layer.get("geometry_item")
        if geometry_item is not None:
            items.append(geometry_item)

        for item in set(items):
            if isinstance(item, pg.ScatterPlotItem):
                item.setBrush(
                    pg.mkBrush(color=new_color)
                )
                item.setPen(
                    pg.mkPen(
                        color=new_color,
                        width=1,
                    )
                )

            elif isinstance(item, pg.PlotCurveItem):
                item.setPen(
                    pg.mkPen(
                        color=new_color,
                        width=1,
                    )
                )


    def set_layer_visibility(self, layer_id: str, visible: bool):
        layer = self.loaded_layers.get(layer_id)

        if layer is None:
            return

        layer['visible'] = visible

        items = list(layer.get("items", []))
        items.extend(
            item
            for item in layer.get("lod_items", {}).values()
            if item is not None
        )

        geometry_item = layer.get("geometry_item")
        if geometry_item is not None:
            items.append(geometry_item)

        for item in set(items):
            item.setVisible(visible)

        if visible:
            self._switch_visible_lod()


    # ------------------------------------------------------------------
    # Stage 4: navigation helpers
    # ------------------------------------------------------------------

    def fit_all_layers(self):
        """Fit the view to all currently loaded layers."""
        if not self.loaded_layers:
            self.status_message.emit("No layers loaded to fit.")
            return

        bounds = []
        for layer in self.loaded_layers.values():
            gdf = layer.get("gdf")
            if gdf is None:
                continue
            try:
                b = gdf.total_bounds
                if len(b) == 4 and np.all(np.isfinite(b)) and b[2] >= b[0] and b[3] >= b[1]:
                    bounds.append(b)
            except Exception:
                continue

        if not bounds:
            # Point-only layers do not keep a GeoDataFrame in the layer
            # metadata, so derive the range from their graphics instead.
            rect = None
            for layer in self.loaded_layers.values():
                for item in layer.get("items", []):
                    try:
                        r = item.boundingRect()
                    except Exception:
                        continue
                    rect = r if rect is None else rect.united(r)
            if rect is None or not rect.isValid():
                self.status_message.emit("Unable to determine layer extent.")
                return
            self.setRange(xRange=(rect.left(), rect.right()),
                          yRange=(rect.top(), rect.bottom()),
                          padding=0.08)
        else:
            arr = np.asarray(bounds, dtype=float)
            xmin = float(np.min(arr[:, 0]))
            ymin = float(np.min(arr[:, 1]))
            xmax = float(np.max(arr[:, 2]))
            ymax = float(np.max(arr[:, 3]))

            if xmax <= xmin:
                xmax = xmin + 1.0
            if ymax <= ymin:
                ymax = ymin + 1.0

            self.setRange(
                xRange=(xmin, xmax),
                yRange=(ymin, ymax),
                padding=0.08,
            )

        self.status_message.emit("View fitted to all active layers.")

    def zoom_to_layer(self, layer_id: str):
        """Zoom the canvas to one layer."""
        layer = self.loaded_layers.get(layer_id)
        if layer is None:
            return

        gdf = layer.get("gdf")
        if gdf is not None:
            try:
                b = gdf.total_bounds
                if len(b) == 4 and np.all(np.isfinite(b)):
                    xmin, ymin, xmax, ymax = map(float, b)
                    if xmax <= xmin:
                        xmax = xmin + 1.0
                    if ymax <= ymin:
                        ymax = ymin + 1.0
                    self.setRange(
                        xRange=(xmin, xmax),
                        yRange=(ymin, ymax),
                        padding=0.08,
                    )
                    self.status_message.emit(f"Zoomed to {layer['name']}.")
                    return
            except Exception:
                pass

        rect = None
        for item in layer.get("items", []):
            try:
                r = item.boundingRect()
            except Exception:
                continue
            rect = r if rect is None else rect.united(r)

        if rect is not None and rect.isValid():
            self.setRange(
                xRange=(rect.left(), rect.right()),
                yRange=(rect.top(), rect.bottom()),
                padding=0.08,
            )
            self.status_message.emit(f"Zoomed to {layer['name']}.")

    def _mouse_moved(self, scene_pos):
        """Emit map coordinates for the status bar while the cursor is over the canvas."""
        if not self.sceneBoundingRect().contains(scene_pos):
            return

        try:
            point = self.getViewBox().mapSceneToView(scene_pos)
            x = float(point.x())
            y = float(point.y())
            if np.isfinite(x) and np.isfinite(y):
                self.status_message.emit(f"X: {x:,.3f}    Y: {y:,.3f}")
        except Exception:
            pass

    def clear_all_layers(self):
        self._lod_generation += 1
        self._lod_switch_timer.stop()

        # Clearing the scene removes graphics items. Worker results are
        # ignored after generation changes.
        self.clear()

        self.showGrid(x=True, y=True, alpha=0.3)
        self.loaded_layers.clear()
        self.color_index = 0

        self.layers_cleared.emit()
        self.status_message.emit(
            "Cleared all QC map layers."
        )


class PipelineRouteCreator(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pipeline Route Shapefile Generator & Fast QC")
        self.resize(1080, 680)
        self.setStyleSheet(bouquet_studio_stylesheet())

        # Stage 4 navigation toolbar.
        navigation_toolbar = QToolBar("Map Navigation", self)
        navigation_toolbar.setMovable(False)
        navigation_toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(Qt.TopToolBarArea, navigation_toolbar)

        fit_action = QAction("Fit All", self)
        fit_action.setToolTip("Fit the map to all loaded layers")
        fit_action.triggered.connect(lambda: self.map_canvas.fit_all_layers())
        navigation_toolbar.addAction(fit_action)

        reset_action = QAction("Reset View", self)
        reset_action.setToolTip("Reset the map view")
        reset_action.triggered.connect(lambda: self.map_canvas.enableAutoRange())
        navigation_toolbar.addAction(reset_action)

        navigation_toolbar.addSeparator()

        zoom_in_action = QAction("Zoom In", self)
        zoom_in_action.triggered.connect(lambda: self.map_canvas.getViewBox().scaleBy((0.5, 0.5)))
        navigation_toolbar.addAction(zoom_in_action)

        zoom_out_action = QAction("Zoom Out", self)
        zoom_out_action.triggered.connect(lambda: self.map_canvas.getViewBox().scaleBy((2.0, 2.0)))
        navigation_toolbar.addAction(zoom_out_action)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QHBoxLayout(central_widget)

        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter)

        # Left Control Panel
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)

        left_layout.addWidget(QLabel("Paste Coordinates (Easting Northing):"))
        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText("Paste columns here...\n612345.0  5845000.0\n612350.0  5845010.0")
        left_layout.addWidget(self.text_edit)

        # Geometry Type Dropdown
        self.geom_combo = QComboBox()
        self.geom_combo.addItem("Points")
        self.geom_combo.addItem("Line (LineString)")
        left_layout.addWidget(QLabel("Export Geometry Type:"))
        left_layout.addWidget(self.geom_combo)

        # CRS Dropdown
        self.crs_combo = QComboBox()
        self.crs_combo.addItem("ED50 / UTM 31N (EPSG:23031)", "EPSG:23031")
        self.crs_combo.addItem("ED50 / UTM 32N (EPSG:23032)", "EPSG:23032")
        self.crs_combo.addItem("WGS 84 (EPSG:4326)", "EPSG:4326")
        left_layout.addWidget(QLabel("Select Active Coordinate System:"))
        left_layout.addWidget(self.crs_combo)

        self.export_btn = QPushButton("Generate Shapefile")
        self.export_btn.clicked.connect(self.generate_shapefile)
        left_layout.addWidget(self.export_btn)

        # Layer Management QListWidget
        left_layout.addWidget(QLabel("Active Layers (Right-click to change color):"))
        self.layer_list = QListWidget()
        self.layer_list.itemChanged.connect(self.on_layer_item_changed)

        self.layer_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.layer_list.customContextMenuRequested.connect(self.show_layer_context_menu)

        left_layout.addWidget(self.layer_list)

        self.clear_map_btn = QPushButton("Clear QC Canvas")
        self.clear_map_btn.clicked.connect(self.clear_canvas)
        left_layout.addWidget(self.clear_map_btn)

        # Right Panel: pyqtgraph Canvas
        self.map_canvas = QCMapCanvas(self)

        splitter.addWidget(left_panel)
        splitter.addWidget(self.map_canvas)
        splitter.setSizes([380, 700])

        # Signal connections
        self.statusBar().showMessage("Ready. Paste coordinates or drag-and-drop multiple .shp files.")
        self.map_canvas.status_message.connect(self.statusBar().showMessage)
        self.map_canvas.layer_added.connect(self.add_layer_to_list)
        self.map_canvas.layers_cleared.connect(self.layer_list.clear)

    def get_active_crs(self) -> str:
        return self.crs_combo.currentData()

    def add_layer_to_list(self, layer_id: str, display_name: str, color_hex: str):
        item = QListWidgetItem(make_color_icon(color_hex), display_name)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked)
        item.setData(Qt.UserRole, layer_id)
        self.layer_list.addItem(item)

    def on_layer_item_changed(self, item: QListWidgetItem):
        layer_id = item.data(Qt.UserRole)
        if layer_id:
            is_visible = item.checkState() == Qt.Checked
            self.map_canvas.set_layer_visibility(layer_id, is_visible)

    def show_layer_context_menu(self, pos):
        item = self.layer_list.itemAt(pos)
        if not item:
            return

        menu = QMenu(self)
        zoom_action = menu.addAction("Zoom to Layer")
        change_color_action = menu.addAction("Change Color...")

        action = menu.exec(self.layer_list.mapToGlobal(pos))

        if action == zoom_action:
            layer_id = item.data(Qt.UserRole)
            if layer_id:
                self.map_canvas.zoom_to_layer(layer_id)
        elif action == change_color_action:
            self.change_item_color(item)

    def change_item_color(self, item: QListWidgetItem):
        layer_id = item.data(Qt.UserRole)
        if not layer_id:
            return

        color = QColorDialog.getColor(parent=self, title="Select Layer Color")
        if color.isValid():
            color_hex = color.name()
            item.setIcon(make_color_icon(color_hex))
            self.map_canvas.change_layer_color(layer_id, color_hex)

    def generate_shapefile(self):
        raw_text = self.text_edit.toPlainText().strip()
        if not raw_text:
            self.statusBar().showMessage("Error: No coordinate input found.")
            return

        points = []
        for line in raw_text.split('\n'):
            parts = line.strip().split()
            if len(parts) >= 2:
                try:
                    easting = float(parts[0])
                    northing = float(parts[1])
                    points.append((easting, northing))
                except ValueError:
                    continue

        if not points:
            self.statusBar().showMessage("Error: No valid numerical coordinate pairs parsed.")
            return

        selected_crs = self.get_active_crs()
        geom_type = self.geom_combo.currentText()

        if geom_type == "Points":
            geometries = [Point(x, y) for x, y in points]
            gdf = gpd.GeoDataFrame({'geometry': geometries}, crs=selected_crs)
        else:
            if len(points) < 2:
                self.statusBar().showMessage("Error: Need at least 2 coordinate pairs to create a Line.")
                return
            line_geom = LineString(points)
            gdf = gpd.GeoDataFrame({'geometry': [line_geom]}, crs=selected_crs)

        save_path, _ = QFileDialog.getSaveFileName(
            self, "Save Shapefile", "", "Shapefiles (*.shp)"
        )

        if save_path:
            try:
                gdf.to_file(save_path)
                self.map_canvas.add_shapefile_layer(save_path, target_crs=selected_crs)
                filename = Path(save_path).name
                self.statusBar().showMessage(f"Exported {geom_type} to {filename} ({selected_crs}).")
            except Exception as e:
                self.statusBar().showMessage(f"Export failed: {str(e)}")
        else:
            self.statusBar().showMessage("Export cancelled.")

    def clear_canvas(self):
        self.map_canvas.clear_all_layers()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PipelineRouteCreator()
    window.show()
    sys.exit(app.exec())