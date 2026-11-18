import geopandas as gpd
import pandas as pd
from glob import glob
import numpy as np

pd.options.display.max_columns = 100


def read_gdf(path: str) -> gpd.GeoDataFrame:
    if path.endswith(".parquet"):
        gdf = gpd.read_parquet(path)
    else:
        gdf = gpd.read_file(path)
    gdf = gdf.reset_index(drop=True)
    gdf = gdf.to_crs(2039)
    return gdf


def main(dir,hour):
    speeds = read_gdf(fr"{dir}/weekdays_{hour}_oclock.parquet")
    speeds20 = read_gdf(rf"{dir}/weekdays_20_oclock.parquet")
    streets = read_gdf(
        r"T:\RTS\Israel\Israel_September_2024\shp files\4326\outputs\output_street_nets_coords\street_net_2024.parquet"
    )

    speeds["from"] = speeds["from"].str.replace("str", "")
    speeds["to"] = speeds["to"].str.replace("str", "")
    speeds20["from"] = speeds20["from"].str.replace("str", "")
    speeds20["to"] = speeds20["to"].str.replace("str", "")
    speeds = speeds.merge(streets[["ROADFUNCTI", "from", "to"]], on=["from", "to"], how="left")
    speeds = speeds.merge(speeds20[["from", "to",'speed_kmh_median']], on=["from", "to"], how="left",suffixes=('','_20'))
    speeds['ration_speed'] = speeds['speed_kmh_median']/ speeds['speed_kmh_median_20']
    speeds['ration_speed'] = speeds['ration_speed'].round(2)
    
    speeds.to_parquet(
        fr"T:\GIS\Speeds\Hasadna\2024\09\stride-siri\Israel\hour_monthly_avg\weekdays_{hour}_oclock_with_road_function.parquet"
    )


if __name__ == "__main__":
    dir =r'T:\GIS\Speeds\Hasadna\2024\09\stride-siri\Israel\hour_monthly_avg'
    hour = 8