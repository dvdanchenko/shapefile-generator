"""Qt table models used by PipelineRouteCreator."""

from PySide6.QtCore import Qt, QAbstractTableModel, QSortFilterProxyModel, QModelIndex

class _AttributeTableModel(QAbstractTableModel):
    """Read-only table model backed directly by a layer GeoDataFrame."""

    def __init__(self, gdf, geometry_column, parent=None):
        super().__init__(parent)
        self.gdf = gdf
        self.geometry_column = geometry_column
        self.columns = [c for c in gdf.columns if c != geometry_column]

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self.gdf)

    def columnCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self.columns) + 1  # feature number + attributes

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            if section == 0:
                return "Feature"
            return str(self.columns[section - 1])
        return str(section + 1)

    @staticmethod
    def _format_value(value):
        if value is None:
            return ""
        if isinstance(value, float):
            if np.isnan(value):
                return ""
            return f"{value:,.6g}"
        return str(value)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        column = index.column()
        if row < 0 or row >= len(self.gdf):
            return None

        if role == Qt.DisplayRole:
            if column == 0:
                return str(row + 1)
            try:
                return self._format_value(self.gdf.iloc[row][self.columns[column - 1]])
            except Exception:
                return ""

        if role == Qt.UserRole:
            if column == 0:
                return row + 1
            try:
                return self.gdf.iloc[row][self.columns[column - 1]]
            except Exception:
                return None

        if role == Qt.TextAlignmentRole and column == 0:
            return Qt.AlignRight | Qt.AlignVCenter

        return None


class _AttributeFilterProxyModel(QSortFilterProxyModel):
    """Case-insensitive search across all displayed attribute columns."""

    def filterAcceptsRow(self, source_row, source_parent):
        pattern = self.filterRegularExpression().pattern().strip().lower()
        if not pattern:
            return True

        model = self.sourceModel()
        for column in range(model.columnCount()):
            index = model.index(source_row, column, source_parent)
            value = model.data(index, Qt.DisplayRole)
            if value is not None and pattern in str(value).lower():
                return True
        return False


