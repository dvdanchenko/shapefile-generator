import sys
from pathlib import Path
import numpy as np
import geopandas as gpd
from shapely.geometry import Point
import pyqtgraph as pg

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPlainTextEdit, QComboBox, QPushButton, QFileDialog, QLabel, 
    QSplitter, QListWidget, QListWidgetItem
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
    """

def make_color_icon(color_hex: str) -> QIcon:
    """Generates a small colored square icon for the QListWidget legend."""
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

    def add_shapefile_layer(self, filepath: str) -> bool:
        path_obj = Path(filepath)
        layer_id = str(path_obj.resolve())

        if layer_id in self.loaded_layers:
            self.status_message.emit(f"Layer '{path_obj.name}' is already loaded.")
            return False

        try:
            gdf = gpd.read_file(filepath)
            if gdf.empty:
                return False

            color = self.PALETTE[self.color_index % len(self.PALETTE)]
            self.color_index += 1

            graphic_items = []

            # Render Points
            point_mask = gdf.geometry.type.isin(['Point'])
            if point_mask.any():
                pts_gdf = gdf[point_mask]
                xs = pts_gdf.geometry.x.to_numpy()
                ys = pts_gdf.geometry.y.to_numpy()
                
                scatter = pg.ScatterPlotItem(
                    x=xs, y=ys, 
                    size=7, 
                    pen=pg.mkPen(color=color, width=1), 
                    brush=pg.mkBrush(color=color),
                    name=path_obj.name
                )
                self.addItem(scatter)
                graphic_items.append(scatter)

            # Render LineStrings
            line_mask = gdf.geometry.type.isin(['LineString'])
            if line_mask.any():
                for line in gdf[line_mask].geometry:
                    x, y = line.xy
                    plot_item = self.plot(np.array(x), np.array(y), pen=pg.mkPen(color=color, width=2))
                    graphic_items.append(plot_item)

            self.loaded_layers[layer_id] = {
                'name': path_obj.name,
                'items': graphic_items,
                'visible': True
            }

            # Emit signal to populate QListWidget
            self.layer_added.emit(layer_id, path_obj.name, color)
            return True

        except Exception as e:
            self.status_message.emit(f"Error loading {path_obj.name}: {str(e)}")
            return False

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
        self.resize(1050, 680)
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

        self.crs_combo = QComboBox()
        self.crs_combo.addItem("ED50 / UTM 31N (EPSG:23031)", "EPSG:23031")
        self.crs_combo.addItem("ED50 / UTM 32N (EPSG:23032)", "EPSG:23032")
        self.crs_combo.addItem("WGS 84 (EPSG:4326)", "EPSG:4326")
        left_layout.addWidget(QLabel("Select Coordinate System:"))
        left_layout.addWidget(self.crs_combo)

        self.export_btn = QPushButton("Generate Shapefile")
        self.export_btn.clicked.connect(self.generate_shapefile)
        left_layout.addWidget(self.export_btn)

        # Layer Management QListWidget
        left_layout.addWidget(QLabel("Active Layers:"))
        self.layer_list = QListWidget()
        self.layer_list.itemChanged.connect(self.on_layer_item_changed)
        left_layout.addWidget(self.layer_list)

        self.clear_map_btn = QPushButton("Clear QC Canvas")
        self.clear_map_btn.clicked.connect(self.clear_canvas)
        left_layout.addWidget(self.clear_map_btn)

        # Right Panel: pyqtgraph Canvas
        self.map_canvas = QCMapCanvas(self)

        splitter.addWidget(left_panel)
        splitter.addWidget(self.map_canvas)
        splitter.setSizes([380, 670])

        # Signal connections
        self.statusBar().showMessage("Ready. Paste coordinates or drag-and-drop multiple .shp files.")
        self.map_canvas.status_message.connect(self.statusBar().showMessage)
        self.map_canvas.layer_added.connect(self.add_layer_to_list)
        self.map_canvas.layers_cleared.connect(self.layer_list.clear)

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
                    points.append(Point(easting, northing))
                except ValueError:
                    continue

        if not points:
            self.statusBar().showMessage("Error: No valid numerical coordinate pairs parsed.")
            return

        selected_crs = self.crs_combo.currentData()
        gdf = gpd.GeoDataFrame({'geometry': points}, crs=selected_crs)

        save_path, _ = QFileDialog.getSaveFileName(
            self, "Save Shapefile", "", "Shapefiles (*.shp)"
        )
        
        if save_path:
            try:
                gdf.to_file(save_path)
                self.map_canvas.add_shapefile_layer(save_path)
                filename = Path(save_path).name
                self.statusBar().showMessage(f"Exported {len(points)} points to {filename} ({selected_crs}).")
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