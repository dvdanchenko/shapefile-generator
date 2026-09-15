import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

def create_shapefile(data_dict, crs, output_filename="output.shp"):
    """
    Creates a ESRI Shapefile bundle with explicit CRS metadata.
    
    Parameters:
        data_dict (dict): Dictionary with 'Easting', 'Northing', and attribute columns.
        crs (str): EPSG code string (e.g., 'EPSG:27700' for British National Grid,
                   'EPSG:32631' for WGS 84 / UTM 31N, or 'EPSG:4326' for WGS 84 Lat/Lon).
        output_filename (str): Name of the output shapefile (must end in .shp).
    """
    # Create pandas DataFrame
    df = pd.DataFrame(data_dict)
    
    # Construct geometry points (X / Easting / Longitude, Y / Northing / Latitude)
    geometry = [Point(x, y) for x, y in zip(df['Easting'], df['Northing'])]
    
    # Build GeoDataFrame with assigned Coordinate Reference System (CRS)
    gdf = gpd.GeoDataFrame(df, geometry=geometry, crs=crs)
    
    # Export to Shapefile bundle (.shp, .shx, .dbf, .prj)
    gdf.to_file(output_filename, driver="ESRI Shapefile")
    print(f"Successfully exported shapefile bundle to '{output_filename}' with CRS: {crs}")


if __name__ == "__main__":
    # Sample coordinate dataset
    sample_data = {
        'Point_ID': [1, 2, 3],
        'Name': ['Station_A', 'Station_B', 'Station_C'],
        'Easting': [500000.0, 501200.0, 502500.0],
        'Northing': [5800000.0, 5801500.0, 5803000.0]
    }
    
    # Example CRS: British National Grid (EPSG:27700) or UTM Zone 31N (EPSG:32631)
    target_crs = "EPSG:27700"
    
    create_shapefile(sample_data, crs=target_crs, output_filename="points.shp")