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
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPlainTextEdit,
    QComboBox,
    QPushButton,
    QFileDialog,
    QLabel,
    QSplitter,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QColorDialog,
    QMenu,
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QPixmap, QColor, QIcon


# ---------------------------------------------------------------------------
# Global pyqtgraph settings
# ---------------------------------------------------------------------------

pg.setConfigOption("background", "#ffffff")
pg.setConfigOption("foreground", "#202020")
pg.setConfigOptions(
    antialias=True,
    useOpenGL=True,
)


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

def bouquet_studio_stylesheet() -> str:
    return """
        QMainWindow {
            background-color: #f2f2f2;
        }

        QWidget {
            color: #202020;
            font-family: Segoe UI, sans-serif;
        }

        QPlainTextEdit {
            background-color: #ffffff;
            border: 1px solid #777777;
            padding: 5px;
        }

        QPushButton {
            background-color: #ffffff;
            border: 1px solid #777777;
            padding: 6px;
            border-radius: 3px;
        }

        QPushButton:hover {
            border: 1px solid cyan;
            background-color: #f8f8f8;
        }

        QComboBox {
            background-color: #ffffff;
            border: 1px solid #777777;
            padding: 4px;
        }

        QListWidget {
            background-color: #ffffff;
            border: 1px solid #777777;
            padding: 3px;
        }

        QListWidget::item {
            padding: 4px;
        }

        QListWidget::item:hover {
            background-color: #f0f0f0;
        }

        QStatusBar {
            background-color: #e0e0e0;
            color: #202020;
            padding-left: 5px;
            border-top: 1px solid #777777;
        }

        QMessageBox {
            background-color: #f2f2f2;
        }

        QMenu {
            background-color: #ffffff;
            border: 1px solid #777777;
        }

        QMenu::item:selected {
            background-color: #e0e0e0;
        }
    """


def make_color_icon(color_hex: str) -> QIcon:
    pixmap = QPixmap(12, 12)
    pixmap.fill(QColor(color_hex))
    return QIcon(pixmap)


# ---------------------------------------------------------------------------
# GIS rendering helpers
# ---------------------------------------------------------------------------

def _simplify_geometry_for_display(geometry, tolerance: float):
    """
    Safely simplify one Shapely geometry for display.

    The original geometry is never modified.
    """
    if geometry is None:
        return geometry

    if geometry.is_empty:
        return geometry

    if tolerance <= 0:
        return geometry

    try:
        return geometry.simplify(
            tolerance,
            preserve_topology=True,
        )
    except Exception:
        # Display simplification must never prevent the layer from loading.
        return geometry


def _build_gis_render_arrays(
    gdf: gpd.GeoDataFrame,
    tolerance: float = 0.0,
):
    """
    Convert line/polygon geometries into NaN-separated NumPy arrays.

    Handles:

        LineString
        MultiLineString
        Polygon
        MultiPolygon

    Polygon exterior rings and interior rings are both rendered.

    NaN values are deliberately used as breaks between independent
    geometries/rings. Therefore PlotCurveItem must use:

        skipFiniteCheck=False
    """

    x_parts = []
    y_parts = []

    for geometry in gdf.geometry:
        if geometry is None:
            continue

        if geometry.is_empty:
            continue

        geometry = _simplify_geometry_for_display(
            geometry,
            tolerance,
        )

        if geometry is None or geometry.is_empty:
            continue

        def append_ring(ring):
            coords = shapely.get_coordinates(ring)

            if len(coords) == 0:
                return

            coords = np.asarray(
                coords,
                dtype=np.float64,
            )

            x_parts.append(coords[:, 0])
            y_parts.append(coords[:, 1])

            # NaN separator.
            x_parts.append(
                np.array([np.nan], dtype=np.float64)
            )
            y_parts.append(
                np.array([np.nan], dtype=np.float64)
            )

        geom_type = geometry.geom_type

        # ---------------------------------------------------------------
        # LineString
        # ---------------------------------------------------------------

        if geom_type == "LineString":
            append_ring(geometry)

        # ---------------------------------------------------------------
        # MultiLineString
        # ---------------------------------------------------------------

        elif geom_type == "MultiLineString":
            for part in geometry.geoms:
                append_ring(part)

        # ---------------------------------------------------------------
        # Polygon
        # ---------------------------------------------------------------

        elif geom_type == "Polygon":
            append_ring(geometry.exterior)

            for interior in geometry.interiors:
                append_ring(interior)

        # ---------------------------------------------------------------
        # MultiPolygon
        # ---------------------------------------------------------------

        elif geom_type == "MultiPolygon":
            for polygon in geometry.geoms:
                append_ring(polygon.exterior)

                for interior in polygon.interiors:
                    append_ring(interior)

    if not x_parts:
        return (
            np.empty(0, dtype=np.float64),
            np.empty(0, dtype=np.float64),
        )

    x = np.concatenate(x_parts)
    y = np.concatenate(y_parts)

    # Remove final separator.
    if len(x) > 0 and np.isnan(x[-1]):
        x = x[:-1]
        y = y[:-1]

    return x, y


# ---------------------------------------------------------------------------
# QC Map Canvas
# ---------------------------------------------------------------------------

class QCMapCanvas(pg.PlotWidget):

    status_message = Signal(str)

    # layer_id, display_name, color_hex
    layer_added = Signal(str, str, str)

    layers_cleared = Signal()

    PALETTE = [
        "#00cccc",
        "#e6007e",
        "#d9a100",
        "#2e7d32",
        "#6a1b9a",
        "#d84315",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setAcceptDrops(True)

        self.showGrid(
            x=True,
            y=True,
            alpha=0.3,
        )

        self.setAspectLocked(True)

        self.getAxis("bottom").setStyle(
            tickTextOffset=8
        )

        self.getAxis("left").setStyle(
            tickTextOffset=8
        )

        # ------------------------------------------------------------------
        # Active layers
        #
        # This structure is deliberately kept compatible with the original
        # Active Layers functionality.
        # ------------------------------------------------------------------

        self.loaded_layers = {}

        self.color_index = 0

        # ------------------------------------------------------------------
        # LOD timer
        #
        # IMPORTANT:
        #
        # LOD is never performed before a layer is registered.
        # This prevents a slow simplification operation from preventing
        # layer_added from being emitted.
        # ------------------------------------------------------------------

        self._lod_timer = QTimer(self)
        self._lod_timer.setSingleShot(True)

        self._lod_timer.timeout.connect(
            self._update_layer_lod
        )

        # Trigger LOD after zoom/pan, but debounce the signal.
        self.getViewBox().sigRangeChanged.connect(
            self._schedule_lod_update
        )

    # ----------------------------------------------------------------------
    # Drag & drop
    # ----------------------------------------------------------------------

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

        shp_files = [
            url.toLocalFile()
            for url in urls
            if url.toLocalFile().lower().endswith(".shp")
        ]

        if not shp_files:

            self.status_message.emit(
                "No valid .shp files found in drop payload."
            )

            return

        loaded_count = 0

        for filepath in shp_files:

            if self.add_shapefile_layer(filepath):
                loaded_count += 1

        self.status_message.emit(
            f"Added {loaded_count} layer(s). "
            f"Active layers: {len(self.loaded_layers)}."
        )

    # ----------------------------------------------------------------------
    # LOD
    # ----------------------------------------------------------------------

    def _display_tolerance(self) -> float:
        """
        Estimate the map distance represented by approximately one screen
        pixel.

        This is only used after a layer has already loaded successfully.
        """

        try:

            x_range, y_range = self.viewRange()

            x_span = abs(
                float(x_range[1]) - float(x_range[0])
            )

            y_span = abs(
                float(y_range[1]) - float(y_range[0])
            )

            width = max(
                1,
                self.viewport().width(),
            )

            height = max(
                1,
                self.viewport().height(),
            )

            return max(
                x_span / width,
                y_span / height,
            ) * 0.75

        except Exception:

            return 0.0

    def _schedule_lod_update(self, *args):
        """
        Debounce repeated pan/zoom signals.

        We do not rebuild the geometry immediately for every mouse movement.
        """

        if not self.loaded_layers:
            return

        self._lod_timer.start(150)

    def _update_layer_lod(self):
        """
        Rebuild complex geometry using a display-dependent simplification.

        The Active Layers dictionary already exists at this point.
        """

        tolerance = self._display_tolerance()

        if tolerance <= 0:
            return

        for layer_id, layer in list(
            self.loaded_layers.items()
        ):

            if not layer.get(
                "visible",
                True,
            ):
                continue

            if not layer.get(
                "has_complex_geometry",
                False,
            ):
                continue

            previous_tolerance = layer.get(
                "render_tolerance"
            )

            if previous_tolerance is not None:

                if previous_tolerance > 0:

                    ratio = (
                        tolerance /
                        previous_tolerance
                    )

                    # Do not rebuild for tiny changes.
                    if 0.5 < ratio < 2.0:
                        continue

            self._render_complex_layer(
                layer_id,
                tolerance,
            )

    def _render_complex_layer(
        self,
        layer_id: str,
        tolerance: float,
    ):
        """
        Replace the rendered line/polygon item for one layer.

        The logical layer remains unchanged.
        """

        layer = self.loaded_layers.get(
            layer_id
        )

        if layer is None:
            return

        gdf = layer.get("gdf")

        if gdf is None or gdf.empty:
            return

        try:

            x, y = _build_gis_render_arrays(
                gdf,
                tolerance,
            )

        except Exception as exc:

            self.status_message.emit(
                f"Display simplification failed for "
                f"{layer['name']}: {exc}"
            )

            return

        old_item = layer.get(
            "geometry_item"
        )

        if old_item is not None:

            try:
                self.removeItem(old_item)
            except Exception:
                pass

        new_item = None

        if x.size:

            new_item = pg.PlotCurveItem(
                x,
                y,
                pen=pg.mkPen(
                    color=layer["color"],
                    width=1,
                ),
                connect="finite",
                antialias=False,

                # IMPORTANT:
                #
                # We have intentional NaN separators.
                # Therefore pyqtgraph must perform its finite check.
                skipFiniteCheck=False,
            )

            self.addItem(
                new_item
            )

            # Preserve logical layer visibility.
            new_item.setVisible(
                layer.get(
                    "visible",
                    True,
                )
            )

        layer["geometry_item"] = new_item

        layer["items"] = (
            [new_item]
            if new_item is not None
            else []
        )

        layer["render_tolerance"] = tolerance

    # ----------------------------------------------------------------------
    # Add shapefile
    # ----------------------------------------------------------------------

    def add_shapefile_layer(
        self,
        filepath: str,
        target_crs: str = None,
    ) -> bool:

        start_time = time.perf_counter()

        path_obj = Path(filepath)

        layer_id = str(
            path_obj.resolve()
        )

        # ------------------------------------------------------------------
        # Prevent duplicate layer.
        # ------------------------------------------------------------------

        if layer_id in self.loaded_layers:

            self.status_message.emit(
                f"Layer '{path_obj.name}' is already loaded."
            )

            return False

        # ------------------------------------------------------------------
        # Determine target CRS.
        # ------------------------------------------------------------------

        if (
            target_crs is None
            and hasattr(
                self.window(),
                "get_active_crs",
            )
        ):

            target_crs = (
                self.window()
                .get_active_crs()
            )

        try:

            # --------------------------------------------------------------
            # Read file
            # --------------------------------------------------------------

            try:

                gdf = gpd.read_file(
                    filepath,
                    engine="pyogrio",
                )

            except Exception:

                gdf = gpd.read_file(
                    filepath
                )

            if gdf.empty:

                self.status_message.emit(
                    f"Layer '{path_obj.name}' is empty."
                )

                return False

            # --------------------------------------------------------------
            # CRS validation
            # --------------------------------------------------------------

            if target_crs:

                if gdf.crs is not None:

                    file_crs_obj = CRS.from_user_input(
                        gdf.crs
                    )

                    target_crs_obj = CRS.from_user_input(
                        target_crs
                    )

                    if file_crs_obj != target_crs_obj:

                        reply = QMessageBox.question(
                            self,
                            "CRS Mismatch Detected",

                            f"Layer '{path_obj.name}' CRS is:\n"
                            f"  • {file_crs_obj.name}\n\n"

                            f"Active Application CRS is:\n"
                            f"  • {target_crs_obj.name} "
                            f"({target_crs})\n\n"

                            "Would you like to reproject this "
                            "layer to match the active system?",

                            QMessageBox.Yes
                            | QMessageBox.No
                            | QMessageBox.Cancel,

                            QMessageBox.Yes,
                        )

                        if reply == QMessageBox.Yes:

                            gdf = gdf.to_crs(
                                target_crs
                            )

                            self.status_message.emit(
                                f"Reprojected "
                                f"{path_obj.name} "
                                f"to {target_crs}."
                            )

                        elif reply == QMessageBox.Cancel:

                            self.status_message.emit(
                                f"Cancelled loading "
                                f"{path_obj.name}."
                            )

                            return False

                else:

                    reply = QMessageBox.warning(
                        self,
                        "Missing CRS (.prj file)",

                        f"The file '{path_obj.name}' "
                        "has no projection metadata "
                        "(.prj file missing).\n\n"

                        f"Assume active CRS "
                        f"({target_crs})?",

                        QMessageBox.Yes
                        | QMessageBox.No,

                        QMessageBox.Yes,
                    )

                    if reply == QMessageBox.Yes:

                        gdf.set_crs(
                            target_crs,
                            inplace=True,
                        )

                    else:

                        return False

            # --------------------------------------------------------------
            # Select layer colour.
            # --------------------------------------------------------------

            color = self.PALETTE[
                self.color_index
                % len(self.PALETTE)
            ]

            self.color_index += 1

            # Graphics items belonging to this logical layer.
            graphic_items = []

            # --------------------------------------------------------------
            # 1. POINT RENDERING
            # --------------------------------------------------------------

            point_mask = (
                gdf.geometry.type.isin(
                    [
                        "Point",
                        "MultiPoint",
                    ]
                )
            )

            if point_mask.any():

                pts = (
                    gdf.geometry[
                        point_mask
                    ]
                    .explode(
                        index_parts=False
                    )
                )

                coords = shapely.get_coordinates(
                    pts.values
                )

                if len(coords):

                    scatter = pg.ScatterPlotItem(
                        x=coords[:, 0],
                        y=coords[:, 1],

                        size=6,

                        pen=pg.mkPen(
                            color=color,
                            width=1,
                        ),

                        brush=pg.mkBrush(
                            color=color
                        ),

                        name=path_obj.name,
                    )

                    self.addItem(
                        scatter
                    )

                    graphic_items.append(
                        scatter
                    )

            # --------------------------------------------------------------
            # 2. LINE & POLYGON DETECTION
            # --------------------------------------------------------------

            line_mask = (
                gdf.geometry.type.isin(
                    [
                        "LineString",
                        "MultiLineString",
                    ]
                )
            )

            poly_mask = (
                gdf.geometry.type.isin(
                    [
                        "Polygon",
                        "MultiPolygon",
                    ]
                )
            )

            has_complex_geometry = bool(
                line_mask.any()
                or poly_mask.any()
            )

            geometry_item = None

            # --------------------------------------------------------------
            # 3. INITIAL LINE/POLYGON RENDER
            #
            # IMPORTANT:
            #
            # We do NOT simplify here.
            #
            # We do NOT use clipToView.
            #
            # We do NOT use downsampling.
            #
            # We first create the geometry item and register the layer.
            # Only after that does LOD become active.
            # --------------------------------------------------------------

            if has_complex_geometry:

                all_x, all_y = (
                    _build_gis_render_arrays(
                        gdf,
                        tolerance=0.0,
                    )
                )

                if all_x.size:

                    geometry_item = (
                        pg.PlotCurveItem(
                            all_x,
                            all_y,

                            pen=pg.mkPen(
                                color=color,
                                width=1,
                            ),

                            connect="finite",

                            antialias=False,

                            # Intentional NaN separators.
                            skipFiniteCheck=False,
                        )
                    )

                    self.addItem(
                        geometry_item
                    )

                    graphic_items.append(
                        geometry_item
                    )

            # --------------------------------------------------------------
            # 4. REGISTER ACTIVE LAYER
            #
            # This happens BEFORE any LOD operation.
            #
            # Therefore the Active Layers QListWidget always gets its signal
            # even if the later display simplification has a problem.
            # --------------------------------------------------------------

            self.loaded_layers[layer_id] = {

                "name": path_obj.name,

                "items": graphic_items,

                "geometry_item": geometry_item,

                # Keep GDF for post-load LOD.
                "gdf": (
                    gdf
                    if has_complex_geometry
                    else None
                ),

                "has_complex_geometry":
                    has_complex_geometry,

                "render_tolerance": None,

                "color": color,

                "visible": True,
            }

            # --------------------------------------------------------------
            # Tell MainWindow to add the layer to Active Layers list.
            # --------------------------------------------------------------

            self.layer_added.emit(
                layer_id,
                path_obj.name,
                color,
            )

            elapsed = (
                time.perf_counter()
                - start_time
            )

            geometry_count = len(gdf)

            try:

                vertex_count = int(
                    sum(
                        len(
                            shapely.get_coordinates(
                                geom
                            )
                        )
                        for geom in gdf.geometry
                        if geom is not None
                        and not geom.is_empty
                    )
                )

            except Exception:

                vertex_count = 0

            self.status_message.emit(
                f"Loaded {path_obj.name}: "
                f"{geometry_count:,} feature(s), "
                f"{vertex_count:,} vertices "
                f"in {elapsed:.2f}s."
            )

            # --------------------------------------------------------------
            # IMPORTANT:
            #
            # Schedule LOD only AFTER registration and signal emission.
            # --------------------------------------------------------------

            if has_complex_geometry:

                self._lod_timer.start(
                    250
                )

            return True

        except Exception as e:

            self.status_message.emit(
                f"Error loading "
                f"{path_obj.name}: "
                f"{str(e)}"
            )

            return False

    # ----------------------------------------------------------------------
    # Change layer colour
    # ----------------------------------------------------------------------

    def change_layer_color(
        self,
        layer_id: str,
        new_color: str,
    ):

        if layer_id not in self.loaded_layers:
            return

        layer = self.loaded_layers[
            layer_id
        ]

        for item in layer["items"]:

            if isinstance(
                item,
                pg.ScatterPlotItem,
            ):

                item.setBrush(
                    pg.mkBrush(
                        color=new_color
                    )
                )

                item.setPen(
                    pg.mkPen(
                        color=new_color,
                        width=1,
                    )
                )

            elif isinstance(
                item,
                pg.PlotCurveItem,
            ):

                item.setPen(
                    pg.mkPen(
                        color=new_color,
                        width=1,
                    )
                )

        # Store new colour so future LOD-created items
        # use the same colour.
        layer["color"] = new_color

    # ----------------------------------------------------------------------
    # Layer visibility
    # ----------------------------------------------------------------------

    def set_layer_visibility(
        self,
        layer_id: str,
        visible: bool,
    ):

        if layer_id not in self.loaded_layers:
            return

        layer = self.loaded_layers[
            layer_id
        ]

        layer["visible"] = visible

        for item in layer["items"]:

            item.setVisible(
                visible
            )

    # ----------------------------------------------------------------------
    # Clear all layers
    # ----------------------------------------------------------------------

    def clear_all_layers(self):

        # Stop any pending LOD operation.
        self._lod_timer.stop()

        self.clear()

        self.showGrid(
            x=True,
            y=True,
            alpha=0.3,
        )

        self.loaded_layers.clear()

        self.color_index = 0

        self.layers_cleared.emit()

        self.status_message.emit(
            "Cleared all QC map layers."
        )


# ---------------------------------------------------------------------------
# Main Window
# ---------------------------------------------------------------------------

class PipelineRouteCreator(QMainWindow):

    def __init__(self):

        super().__init__()

        self.setWindowTitle(
            "Pipeline Route Shapefile Generator & Fast QC"
        )

        self.resize(
            1080,
            680,
        )

        self.setStyleSheet(
            bouquet_studio_stylesheet()
        )

        # ------------------------------------------------------------------
        # Central widget
        # ------------------------------------------------------------------

        central_widget = QWidget()

        self.setCentralWidget(
            central_widget
        )

        layout = QHBoxLayout(
            central_widget
        )

        splitter = QSplitter(
            Qt.Horizontal
        )

        layout.addWidget(
            splitter
        )

        # ------------------------------------------------------------------
        # Left control panel
        # ------------------------------------------------------------------

        left_panel = QWidget()

        left_layout = QVBoxLayout(
            left_panel
        )

        # ------------------------------------------------------------------
        # Coordinate input
        # ------------------------------------------------------------------

        left_layout.addWidget(
            QLabel(
                "Paste Coordinates "
                "(Easting Northing):"
            )
        )

        self.text_edit = (
            QPlainTextEdit()
        )

        self.text_edit.setPlaceholderText(
            "Paste columns here...\n"
            "612345.0  5845000.0\n"
            "612350.0  5845010.0"
        )

        left_layout.addWidget(
            self.text_edit
        )

        # ------------------------------------------------------------------
        # Geometry type
        # ------------------------------------------------------------------

        self.geom_combo = (
            QComboBox()
        )

        self.geom_combo.addItem(
            "Points"
        )

        self.geom_combo.addItem(
            "Line (LineString)"
        )

        left_layout.addWidget(
            QLabel(
                "Export Geometry Type:"
            )
        )

        left_layout.addWidget(
            self.geom_combo
        )

        # ------------------------------------------------------------------
        # CRS
        # ------------------------------------------------------------------

        self.crs_combo = (
            QComboBox()
        )

        self.crs_combo.addItem(
            "ED50 / UTM 31N "
            "(EPSG:23031)",
            "EPSG:23031",
        )

        self.crs_combo.addItem(
            "ED50 / UTM 32N "
            "(EPSG:23032)",
            "EPSG:23032",
        )

        self.crs_combo.addItem(
            "WGS 84 "
            "(EPSG:4326)",
            "EPSG:4326",
        )

        left_layout.addWidget(
            QLabel(
                "Select Active "
                "Coordinate System:"
            )
        )

        left_layout.addWidget(
            self.crs_combo
        )

        # ------------------------------------------------------------------
        # Generate shapefile
        # ------------------------------------------------------------------

        self.export_btn = (
            QPushButton(
                "Generate Shapefile"
            )
        )

        self.export_btn.clicked.connect(
            self.generate_shapefile
        )

        left_layout.addWidget(
            self.export_btn
        )

        # ------------------------------------------------------------------
        # Active Layers
        # ------------------------------------------------------------------

        left_layout.addWidget(
            QLabel(
                "Active Layers "
                "(Right-click to change color):"
            )
        )

        self.layer_list = (
            QListWidget()
        )

        self.layer_list.itemChanged.connect(
            self.on_layer_item_changed
        )

        self.layer_list.setContextMenuPolicy(
            Qt.CustomContextMenu
        )

        self.layer_list.customContextMenuRequested.connect(
            self.show_layer_context_menu
        )

        left_layout.addWidget(
            self.layer_list
        )

        # ------------------------------------------------------------------
        # Clear button
        # ------------------------------------------------------------------

        self.clear_map_btn = (
            QPushButton(
                "Clear QC Canvas"
            )
        )

        self.clear_map_btn.clicked.connect(
            self.clear_canvas
        )

        left_layout.addWidget(
            self.clear_map_btn
        )

        # ------------------------------------------------------------------
        # Right panel
        # ------------------------------------------------------------------

        self.map_canvas = (
            QCMapCanvas(self)
        )

        splitter.addWidget(
            left_panel
        )

        splitter.addWidget(
            self.map_canvas
        )

        splitter.setSizes(
            [
                380,
                700,
            ]
        )

        # ------------------------------------------------------------------
        # Signals
        # ------------------------------------------------------------------

        self.statusBar().showMessage(
            "Ready. Paste coordinates or "
            "drag-and-drop multiple .shp files."
        )

        self.map_canvas.status_message.connect(
            self.statusBar().showMessage
        )

        # IMPORTANT:
        # This is the signal that populates Active Layers.
        self.map_canvas.layer_added.connect(
            self.add_layer_to_list
        )

        self.map_canvas.layers_cleared.connect(
            self.layer_list.clear
        )

    # ----------------------------------------------------------------------
    # Active CRS
    # ----------------------------------------------------------------------

    def get_active_crs(self) -> str:

        return self.crs_combo.currentData()

    # ----------------------------------------------------------------------
    # Add layer to Active Layers list
    # ----------------------------------------------------------------------

    def add_layer_to_list(
        self,
        layer_id: str,
        display_name: str,
        color_hex: str,
    ):

        item = QListWidgetItem(
            make_color_icon(
                color_hex
            ),
            display_name,
        )

        item.setFlags(
            item.flags()
            | Qt.ItemIsUserCheckable
        )

        item.setCheckState(
            Qt.Checked
        )

        item.setData(
            Qt.UserRole,
            layer_id,
        )

        self.layer_list.addItem(
            item
        )

    # ----------------------------------------------------------------------
    # Layer checkbox
    # ----------------------------------------------------------------------

    def on_layer_item_changed(
        self,
        item: QListWidgetItem,
    ):

        layer_id = item.data(
            Qt.UserRole
        )

        if layer_id:

            is_visible = (
                item.checkState()
                == Qt.Checked
            )

            self.map_canvas.set_layer_visibility(
                layer_id,
                is_visible,
            )

    # ----------------------------------------------------------------------
    # Context menu
    # ----------------------------------------------------------------------

    def show_layer_context_menu(
        self,
        pos,
    ):

        item = self.layer_list.itemAt(
            pos
        )

        if not item:
            return

        menu = QMenu(
            self
        )

        change_color_action = (
            menu.addAction(
                "Change Color..."
            )
        )

        action = menu.exec(
            self.layer_list.mapToGlobal(
                pos
            )
        )

        if action == change_color_action:

            self.change_item_color(
                item
            )

    # ----------------------------------------------------------------------
    # Change layer colour
    # ----------------------------------------------------------------------

    def change_item_color(
        self,
        item: QListWidgetItem,
    ):

        layer_id = item.data(
            Qt.UserRole
        )

        if not layer_id:
            return

        color = QColorDialog.getColor(
            parent=self,
            title="Select Layer Color",
        )

        if color.isValid():

            color_hex = color.name()

            item.setIcon(
                make_color_icon(
                    color_hex
                )
            )

            self.map_canvas.change_layer_color(
                layer_id,
                color_hex,
            )

    # ----------------------------------------------------------------------
    # Generate shapefile
    # ----------------------------------------------------------------------

    def generate_shapefile(self):

        raw_text = (
            self.text_edit
            .toPlainText()
            .strip()
        )

        if not raw_text:

            self.statusBar().showMessage(
                "Error: No coordinate input found."
            )

            return

        points = []

        for line in raw_text.split(
            "\n"
        ):

            parts = (
                line.strip()
                .split()
            )

            if len(parts) >= 2:

                try:

                    easting = float(
                        parts[0]
                    )

                    northing = float(
                        parts[1]
                    )

                    points.append(
                        (
                            easting,
                            northing,
                        )
                    )

                except ValueError:

                    continue

        if not points:

            self.statusBar().showMessage(
                "Error: No valid numerical "
                "coordinate pairs parsed."
            )

            return

        selected_crs = (
            self.get_active_crs()
        )

        geom_type = (
            self.geom_combo.currentText()
        )

        # ------------------------------------------------------------------
        # Create geometry
        # ------------------------------------------------------------------

        if geom_type == "Points":

            geometries = [
                Point(x, y)
                for x, y in points
            ]

            gdf = gpd.GeoDataFrame(
                {
                    "geometry": geometries
                },
                crs=selected_crs,
            )

        else:

            if len(points) < 2:

                self.statusBar().showMessage(
                    "Error: Need at least 2 "
                    "coordinate pairs to create "
                    "a Line."
                )

                return

            line_geom = LineString(
                points
            )

            gdf = gpd.GeoDataFrame(
                {
                    "geometry": [
                        line_geom
                    ]
                },
                crs=selected_crs,
            )

        # ------------------------------------------------------------------
        # Save
        # ------------------------------------------------------------------

        save_path, _ = (
            QFileDialog.getSaveFileName(
                self,
                "Save Shapefile",
                "",
                "Shapefiles (*.shp)",
            )
        )

        if save_path:

            try:

                gdf.to_file(
                    save_path
                )

                self.map_canvas.add_shapefile_layer(
                    save_path,
                    target_crs=selected_crs,
                )

                filename = Path(
                    save_path
                ).name

                self.statusBar().showMessage(
                    f"Exported {geom_type} "
                    f"to {filename} "
                    f"({selected_crs})."
                )

            except Exception as e:

                self.statusBar().showMessage(
                    f"Export failed: {str(e)}"
                )

        else:

            self.statusBar().showMessage(
                "Export cancelled."
            )

    # ----------------------------------------------------------------------
    # Clear canvas
    # ----------------------------------------------------------------------

    def clear_canvas(self):

        self.map_canvas.clear_all_layers()


# ---------------------------------------------------------------------------
# Application entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    app = QApplication(
        sys.argv
    )

    window = (
        PipelineRouteCreator()
    )

    window.show()

    sys.exit(
        app.exec()
    )