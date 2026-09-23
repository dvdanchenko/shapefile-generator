import sys
import geopandas as gpd
from shapely.geometry import Point
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPlainTextEdit, QComboBox, QPushButton, QFileDialog, QLabel, QSplitter, QStatusBar
)
from PySide6.QtCore import Qt, Signal
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

# --- Custom Stylesheet ---
def bouquet_studio_stylesheet(is_serious: bool = False) -> str:
    """Returns the studio chrome stylesheet with Van Gogh / Yellow House accents."""
    return """
        QMainWindow { background-color: #f2f2f2; }
        QWidget { color: #202020; font-family: Segoe UI, sans-serif; }
        QPlainTextEdit { background-color: #ffffff; border: 1px solid #777777; padding: 5px; }
        QPushButton { background-color: #ffffff; border: 1px solid #777777; padding: 6px; border-radius: 3px; }
        QPushButton:hover { border: 1px solid cyan; background-color: #f8f8f8; }
        QComboBox { background-color: #ffffff; border: 1px solid #777777; padding: 4px; }
        QStatusBar { background-color: #e0e0e0; color: #202020; padding-left: 5px; border-top: 1px solid #777777; }
    """

# --- Drag & Drop Matplotlib Canvas ---
class QCMapCanvas(FigureCanvas):
    # Create a custom signal to send status messages back to the main window
    status_message = Signal(str)

    def __init__(self, parent=None):
        fig = Figure(figsize=(5, 5), dpi=100)
        self.axes = fig.add_subplot(111)
        self.axes.set_title("Drag and Drop .shp file here")
        self.axes.axis('off')
        
        super().__init__(fig)
        self.setParent(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            filepath = urls[0].toLocalFile()
            if filepath.endswith('.shp'):
                self.plot_shapefile(filepath)
            else:
                self.status_message.emit("Please drop a valid .shp file.")

    def plot_shapefile(self, filepath):
        self.axes.clear()
        filename = filepath.split('/')[-1]
        try:
            self.status_message.emit(f"Loading {filename}...")
            gdf = gpd.read_file(filepath)
            gdf.plot(ax=self.axes, color='cyan', edgecolor='#202020', markersize=25)
            
            self.axes.set_title(f"QC: {filename}")
            self.axes.axis('on')
            self.axes.ticklabel_format(useOffset=False, style='plain') 
            self.draw()
            
            # Emit success message with feature count
            self.status_message.emit(f"Successfully loaded {len(gdf)} features from {filename}.")
        except Exception as e:
            self.axes.set_title("Error loading file")
            self.draw()
            self.status_message.emit(f"Error loading {filename}: {str(e)}")

# --- Main Window ---
class PipelineRouteCreator(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pipeline Route Shapefile Generator")
        self.resize(900, 600)
        self.setStyleSheet(bouquet_studio_stylesheet())

        # Main Layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QHBoxLayout(central_widget)

        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter)

        # Left Panel: Input & Controls
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

        # Right Panel: QC Map
        self.map_canvas = QCMapCanvas(self)

        splitter.addWidget(left_panel)
        splitter.addWidget(self.map_canvas)
        splitter.setSizes([400, 500])

        # Status Bar Initialization
        self.statusBar().showMessage("Ready. Paste coordinates or drag-and-drop a shapefile.")
        
        # Connect the canvas status signal to the main window's status bar
        self.map_canvas.status_message.connect(self.statusBar().showMessage)

    def generate_shapefile(self):
        raw_text = self.text_edit.toPlainText().strip()
        if not raw_text:
            self.statusBar().showMessage("Error: No coordinates pasted.")
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
            self.statusBar().showMessage("Error: Could not parse any valid numeric coordinates.")
            return

        selected_crs = self.crs_combo.currentData()
        gdf = gpd.GeoDataFrame({'geometry': points}, crs=selected_crs)

        save_path, _ = QFileDialog.getSaveFileName(
            self, "Save Shapefile", "", "Shapefiles (*.shp)"
        )
        
        if save_path:
            try:
                gdf.to_file(save_path)
                filename = save_path.split('/')[-1]
                self.statusBar().showMessage(f"Exported {len(points)} points to {filename} ({selected_crs}).")
                self.map_canvas.plot_shapefile(save_path)
            except Exception as e:
                self.statusBar().showMessage(f"Export failed: {str(e)}")
        else:
            self.statusBar().showMessage("Export cancelled.")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PipelineRouteCreator()
    window.show()
    sys.exit(app.exec())