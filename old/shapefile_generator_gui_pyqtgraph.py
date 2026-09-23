import sys
from pathlib import Path
import numpy as np
import geopandas as gpd
from shapely.geometry import Point
import pyqtgraph as pg

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPlainTextEdit, QComboBox, QPushButton, QFileDialog, QLabel, QSplitter
)
from PySide6.QtCore import Qt, Signal

# Set pyqtgraph global aesthetics before widget creation
pg.setConfigOption('background', '#ffffff')
pg.setConfigOption('foreground', '#202020')
pg.setConfigOptions(antialias=True)

# Studio Chrome Stylesheet
def bouquet_studio_stylesheet() -> str:
    return """
        QMainWindow { background-color: #f2f2f2; }
        QWidget { color: #202020; font-family: Segoe UI, sans-serif; }
        QPlainTextEdit { background-color: #ffffff; border: 1px solid #777777; padding: 5px; }
        QPushButton { background-color: #ffffff; border: 1px solid #777777; padding: 6px; border-radius: 3px; }
        QPushButton:hover { border: 1px solid cyan; background-color: #f8f8f8; }
        QComboBox { background-color: #ffffff; border: 1px solid #777777; padding: 4px; }
        QStatusBar { background-color: #e0e0e0; color: #202020; padding-left: 5px; border-top: 1px solid #777777; }
    """

# --- Drag & Drop pyqtgraph QC Canvas ---
class QCMapCanvas(pg.PlotWidget):
    status_message = Signal(str)

    # Color palette cycling for layer overlays
    PALETTE = ['#00cccc', '#e6007e', '#d9a100', '#2e7d32', '#6a1b9a', '#d84315']

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.showGrid(x=True, y=True, alpha=0.3)
        self.setAspectLocked(True)  # Preserve 1:1 GIS spatial ratio
        
        # Disable scientific notation formatting on axis labels
        self.getAxis('bottom').setStyle(tickTextOffset=8)
        self.getAxis('left').setStyle(tickTextOffset=8)

        self.loaded_layers = {}  # filepath: layer metadata
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
        
        total_features = sum(l['features'] for l in self.loaded_layers.values())
        self.status_message.emit(
            f"Added {loaded_count} layer(s). Total active layers: {len(self.loaded_layers)} ({total_features} features)."
        )

    def add_shapefile_layer(self, filepath: str) -> bool:
        path_obj = Path(filepath)
        if str(path_obj) in self.loaded_layers:
            self.status_message.emit(f"Layer '{path_obj.name}' is already loaded.")
            return False

        try:
            gdf = gpd.read_file(filepath)
            if gdf.empty:
                return False

            color = self.PALETTE[self.color_index % len(self.PALETTE)]
            self.color_index += 1

            feature_count = len(gdf)
            
            # 1. Render Point Geometries
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

            # 2. Render LineString Geometries
            line_mask = gdf.geometry.type.isin(['LineString'])
            if line_mask.any():
                for line in gdf[line_mask].geometry:
                    x, y = line.xy
                    self.plot(np.array(x), np.array(y), pen=pg.mkPen(color=color, width=2))

            self.loaded_layers[str(path_obj)] = {
                'name': path_obj.name,
                'features': feature_count,
                'color': color
            }
            return True

        except Exception as e:
            self.status_message.emit(f"Error loading {path_obj.name}: {str(e)}")
            return False

    def clear_all_layers(self):
        self.clear()
        self.showGrid(x=True, y=True, alpha=0.3)
        self.loaded_layers.clear()
        self.color_index = 0
        self.status_message.emit("Cleared all QC map layers.")


# --- Main Application Window ---
class PipelineRouteCreator(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pipeline Route Shapefile Generator & Fast QC")
        self.resize(1000, 650)
        self.setStyleSheet(bouquet_studio_stylesheet())

        # Central Layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QHBoxLayout(central_widget)

        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter)

        # Left Panel: Coordinate Input & File Export
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

        self.clear_map_btn = QPushButton("Clear QC Canvas")
        self.clear_map_btn.clicked.connect(self.clear_canvas)
        left_layout.addWidget(self.clear_map_btn)

        # Right Panel: pyqtgraph Canvas
        self.map_canvas = QCMapCanvas(self)

        splitter.addWidget(left_panel)
        splitter.addWidget(self.map_canvas)
        splitter.setSizes([380, 620])

        # Status Bar Initialization
        self.statusBar().showMessage("Ready. Paste coordinates or drag-and-drop multiple .shp files.")
        self.map_canvas.status_message.connect(self.statusBar().showMessage)

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
                # Automatically add generated shapefile as a layer without wiping existing ones
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