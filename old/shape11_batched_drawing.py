import sys
from pathlib import Path
import numpy as np
import geopandas as gpd
from shapely.geometry import Point, LineString, Polygon
import pyqtgraph as pg
from pyproj import CRS

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPlainTextEdit, QComboBox, QPushButton, QFileDialog, QLabel, 
    QSplitter, QListWidget, QListWidgetItem, QMessageBox,
    QColorDialog, QMenu
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QColor, QIcon

# Global pyqtgraph settings
pg.setConfigOption('background', '#ffffff')
pg.setConfigOption('foreground', '#202020')
pg.setConfigOptions(antialias=True)

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

        self.loaded_layers = {}  # layer_id: {'items': [pg.GraphicsItem], 'visible': bool}
        self.color_index = 0

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
        shp_files = [url.toLocalFile() for url in urls if url.toLocalFile().endswith('.shp')]
        
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
            # Fast GeoPandas loading
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

            # 1. BATCHED POINT RENDERING
            point_mask = gdf.geometry.type.isin(['Point', 'MultiPoint'])
            if point_mask.any():
                pts_gdf = gdf[point_mask]
                xs, ys = [], []
                for geom in pts_gdf.geometry:
                    if geom.geom_type == 'Point':
                        xs.append(geom.x)
                        ys.append(geom.y)
                    elif geom.geom_type == 'MultiPoint':
                        for pt in geom.geoms:
                            xs.append(pt.x)
                            ys.append(pt.y)
                
                scatter = pg.ScatterPlotItem(
                    x=np.array(xs, dtype=np.float64), 
                    y=np.array(ys, dtype=np.float64), 
                    size=6, 
                    pen=pg.mkPen(color=color, width=1), 
                    brush=pg.mkBrush(color=color),
                    name=path_obj.name
                )
                self.addItem(scatter)
                graphic_items.append(scatter)

            # 2. BATCHED LINE & POLYGON RENDERING (1 item per layer using NaN breaks)
            line_poly_mask = gdf.geometry.type.isin(['LineString', 'MultiLineString', 'Polygon', 'MultiPolygon'])
            if line_poly_mask.any():
                xs_list, ys_list = [], []
                nan_array = np.array([np.nan])

                for geom in gdf[line_poly_mask].geometry:
                    if geom is None or geom.is_empty:
                        continue

                    geoms = [geom] if geom.geom_type in ['LineString', 'Polygon'] else geom.geoms
                    for g in geoms:
                        if hasattr(g, 'exterior'): # Polygon boundary
                            xy = np.array(g.exterior.coords)
                        elif hasattr(g, 'coords'):  # LineString
                            xy = np.array(g.coords)
                        else:
                            continue

                        xs_list.append(xy[:, 0])
                        xs_list.append(nan_array)
                        ys_list.append(xy[:, 1])
                        ys_list.append(nan_array)

                if xs_list:
                    all_x = np.concatenate(xs_list)
                    all_y = np.concatenate(ys_list)
                    
                    # Single PlotCurveItem for hundreds of thousands of lines/polygons
                    curve = pg.PlotCurveItem(
                        all_x, all_y, 
                        pen=pg.mkPen(color=color, width=1.5), 
                        connect='finite' # Tells pyqtgraph to break lines across NaNs
                    )
                    self.addItem(curve)
                    graphic_items.append(curve)

            self.loaded_layers[layer_id] = {
                'name': path_obj.name,
                'items': graphic_items,
                'visible': True
            }

            self.layer_added.emit(layer_id, path_obj.name, color)
            return True

        except Exception as e:
            self.status_message.emit(f"Error loading {path_obj.name}: {str(e)}")
            return False

    def change_layer_color(self, layer_id: str, new_color: str):
        if layer_id not in self.loaded_layers:
            return

        for item in self.loaded_layers[layer_id]['items']:
            if isinstance(item, pg.ScatterPlotItem):
                item.setBrush(pg.mkBrush(color=new_color))
                item.setPen(pg.mkPen(color=new_color, width=1))
            elif isinstance(item, pg.PlotCurveItem):
                item.setPen(pg.mkPen(color=new_color, width=1.5))

    def set_layer_visibility(self, layer_id: str, visible: bool):
        if layer_id in self.loaded_layers:
            self.loaded_layers[layer_id]['visible'] = visible
            for item in self.loaded_layers[layer_id]['items']:
                item.setVisible(visible)

    def clear_all_layers(self):
        self.clear()
        self.showGrid(x=True, y=True, alpha=0.3)
        self.loaded_layers.clear()
        self.color_index = 0
        self.layers_cleared.emit()
        self.status_message.emit("Cleared all QC map layers.")


class PipelineRouteCreator(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pipeline Route Shapefile Generator & Fast QC")
        self.resize(1080, 680)
        self.setStyleSheet(bouquet_studio_stylesheet())

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
        change_color_action = menu.addAction("Change Color...")
        
        action = menu.exec(self.layer_list.mapToGlobal(pos))
        
        if action == change_color_action:
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