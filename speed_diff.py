import pandas as pd
import geopandas as gpd
import osmnx


def get_file(hour,month, year,siri_type,filter_name):
    if siri_type =='hasadna':
        path = rf"T:/GIS/Speeds/Hasadna/{year}/{month}/{filter_name}/hour_monthly_avg/weekdays_{hour}_oclock.parquet"
        file_speed = gpd.read_parquet(path)
    return file_speed



def diff_speed(siri_type , hour = None):
    if not filter_name:
        filter_name ='Israel'
    
    if not hour:
        hour = [x for x in range(0, 24)]

    else:
        if type(hour) != list:
            hour = [hour]

    all_hours= None
    for x in hour:
        print(x)
        avg_month1 = get_file(x,month1, year1,siri_type,filter_name)
        avg_month2 = get_file(x,month2, year2,siri_type,filter_name='Tel Aviv')

        avg_month1 = avg_month1[['from','to','speed_kmh_mean']]
        avg_month2 = avg_month2[['from','to','speed_kmh_mean', 'geometry']]

        hour_merge = avg_month2.merge(avg_month1, on=['from','to'], how='left', suffixes=(f'_month{month2}_hour{x}',f'_month{month1}_hour{x}'))
        hour_merge[f'speed_diff_hour{x}'] = hour_merge[f'speed_kmh_mean_month{month1}_hour{x}'] - hour_merge[f'speed_kmh_mean_month{month2}_hour{x}'] 
        # all_hours.append(hour_merge)
        if  all_hours is  None:
            all_hours = hour_merge.copy()
        else:
            all_hours = all_hours.merge(hour_merge, on=['from','to'], how='outer')
            all_hours['geometry_x'] = all_hours['geometry_x'].fillna(all_hours['geometry_y'])
            all_hours = all_hours.rename(columns={'geometry_x':'geometry'})
            all_hours = all_hours.drop(columns='geometry_y')
    all_hours= gpd.GeoDataFrame(all_hours,crs=2039)
    
    
    filter_geom = osmnx.geocode_to_gdf('Tel Aviv, Israel').to_crs(2039)
    filter_indx, hour_indx = all_hours.sindex.query(filter_geom.geometry, predicate="intersects")
    all_hours = all_hours.iloc[hour_indx]
    
    all_hours.geometry = all_hours.geometry.map(lambda x: x.parallel_offset(5))
    all_hours =all_hours.to_crs(4326)
    
    for i in all_hours.columns:
        if 'speed' in i:
            all_hours[i] = all_hours[i].round(0)
            all_hours[i] = all_hours[i].astype('Int64')
    all_hours.to_file(r'T:\GIS\Speeds\TLV_oct_may_2023_speeds_4326.geojson')
    
    
    
if __name__=="__main__":
    year1 = 2023
    month1 = '05'
    year2 = 2023
    month2 = '09'   
    siri_type = 'hasadna'
    filter_name = None