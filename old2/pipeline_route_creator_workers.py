"""Background workers and geometry rendering helpers for PipelineRouteCreator.

This module contains no Qt graphics-item creation in worker threads.
"""

import numpy as np
import geopandas as gpd
import shapely
from PySide6.QtCore import QObject, QRunnable, Signal

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


class _QCWorkerSignals(QObject):
    finished = Signal(str, int, object)
    failed = Signal(str, int, str)


class _FeatureIndexWorkerSignals(QObject):
    finished = Signal(str, int, object)
    failed = Signal(str, int, str)


class _FeatureIndexWorker(QRunnable):
    """Build a GeoPandas spatial index away from the GUI thread."""

    def __init__(self, layer_id, generation, gdf):
        super().__init__()
        self.layer_id = layer_id
        self.generation = generation
        self.gdf = gdf
        self.signals = _FeatureIndexWorkerSignals()
        self.setAutoDelete(True)

    def run(self):
        try:
            index = self.gdf.sindex
            self.signals.finished.emit(
                self.layer_id, self.generation, index
            )
        except Exception as exc:
            self.signals.failed.emit(
                self.layer_id, self.generation, str(exc)
            )


class _GeometryQCWorker(QRunnable):
    """Run potentially expensive geometry checks away from the GUI thread."""

    def __init__(self, layer_id, generation, gdf):
        super().__init__()
        self.layer_id = layer_id
        self.generation = generation
        self.gdf = gdf
        self.signals = _QCWorkerSignals()
        self.setAutoDelete(True)

    def run(self):
        try:
            geometry = self.gdf.geometry
            feature_count = len(geometry)

            null_mask = geometry.isna()
            null_count = int(null_mask.sum())

            non_null = geometry[~null_mask]
            empty_count = int(non_null.is_empty.sum()) if len(non_null) else 0

            # Validity is calculated only for actual, non-empty geometries.
            valid_count = 0
            invalid_count = 0
            if len(non_null):
                non_empty = non_null[~non_null.is_empty]
                if len(non_empty):
                    valid_mask = shapely.is_valid(non_empty.array)
                    valid_count = int(np.asarray(valid_mask, dtype=bool).sum())
                    invalid_count = int(len(non_empty) - valid_count)

            # Zero-length checks are meaningful for linear geometries.
            zero_length_count = 0
            try:
                linear_mask = non_null.geom_type.isin([
                    "LineString", "MultiLineString"
                ])
                linear = non_null[linear_mask]
                if len(linear):
                    lengths = shapely.length(linear.array)
                    zero_length_count = int(
                        np.count_nonzero(np.asarray(lengths) == 0)
                    )
            except Exception:
                zero_length_count = 0

            # Duplicate geometry check. WKB is used because it provides a
            # stable, exact geometry representation without spatial indexing.
            duplicate_count = 0
            try:
                actual = non_null[~non_null.is_empty]
                if len(actual) > 1:
                    wkb = shapely.to_wkb(actual.array)
                    duplicate_mask = actual.index.to_series().map(
                        dict(zip(actual.index, wkb))
                    )
                    # The mapping above preserves duplicate indices poorly in
                    # some GeoDataFrames, so use the WKB byte values directly.
                    duplicate_count = int(
                        len(wkb) - len({bytes(value) for value in wkb})
                    )
            except Exception:
                duplicate_count = 0

            geometry_types = {}
            try:
                geometry_types = {
                    str(name): int(count)
                    for name, count in geometry.geom_type.fillna("None")
                    .value_counts().items()
                }
            except Exception:
                pass

            issues = []
            if null_count:
                issues.append(f"{null_count:,} null geometry(ies)")
            if invalid_count:
                issues.append(f"{invalid_count:,} invalid geometry(ies)")
            if empty_count:
                issues.append(f"{empty_count:,} empty geometry(ies)")
            if zero_length_count:
                issues.append(f"{zero_length_count:,} zero-length line(s)")
            if duplicate_count:
                issues.append(f"{duplicate_count:,} duplicate geometry(ies)")

            if invalid_count or null_count:
                status = "ERROR"
            elif empty_count or zero_length_count or duplicate_count:
                status = "WARNING"
            else:
                status = "PASS"

            result = {
                "status": status,
                "feature_count": int(feature_count),
                "valid_count": int(valid_count),
                "invalid_count": int(invalid_count),
                "null_count": int(null_count),
                "empty_count": int(empty_count),
                "zero_length_count": int(zero_length_count),
                "duplicate_count": int(duplicate_count),
                "issues": issues,
                "geometry_types": geometry_types,
                "crs_defined": self.gdf.crs is not None,
            }

            if self.gdf.crs is None:
                result["crs_warning"] = "CRS is not defined."
                if status == "PASS":
                    result["status"] = "WARNING"
                    result["issues"].append("CRS is not defined")

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


