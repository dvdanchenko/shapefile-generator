from pathlib import Path
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

def create_shapefile(data_dict, crs, output_dir="output", filename="points.shp"):
    """
    Creates a ESRI Shapefile bundle inside a specified directory.
    
    Parameters:
        data_dict (dict): Dictionary with 'Easting', 'Northing', and attribute columns.
        crs (str): EPSG code string (e.g., 'EPSG:27700', 'EPSG:32631', or 'EPSG:4326').
        output_dir (str/Path): Folder path where files will be saved.
        filename (str): Name of the shapefile (e.g., 'points.shp').
    """
    # 1. Ensure the output directory exists
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    
    # Full output path
    shapefile_filepath = out_path / filename

    # 2. Construct GeoDataFrame
    df = pd.DataFrame(data_dict)
    geometry = [Point(x, y) for x, y in zip(df['Easting'], df['Northing'])]
    gdf = gpd.GeoDataFrame(df, geometry=geometry, crs=crs)
    
    # 3. Export bundle (.shp, .shx, .dbf, .prj) to the target directory
    gdf.to_file(shapefile_filepath, driver="ESRI Shapefile")
    print(f"Exported shapefile bundle to: {shapefile_filepath.resolve()}")


if __name__ == "__main__":
    sample_data = {
        'Point_ID': [1, 2, 3],
        'Name': ['Station_A', 'Station_B', 'Station_C'],
        'Easting': [500000.0, 501200.0, 502500.0],
        'Northing': [5800000.0, 5801500.0, 5803000.0]
    }
    
    # Saves into /output/points.shp
    create_shapefile(
        sample_data, 
        crs="EPSG:27700", 
        output_dir="output", 
        filename="points.shp"
    )