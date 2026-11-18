import os
from dotenv import load_dotenv, find_dotenv
import pandas as pd
import geopandas as gpd
import duckdb
import boto3
import os
import calendar
from collections import defaultdict
from datetime import date, timedelta
from typing import List
import datetime
import hdate
from hdate.holidays import HolidayDatabase

SIRI_COLUMNS = [
    "recordedattime",
    "lineref",
    "dataframeref",
    "datedvehiclejourneyref",
    "operatorref",
    "originaimeddeparturetime",
    "longitude",
    "latitude",
    "bearing",
    "velocity",
    "vehicleref",
    "stoppointref",
    "stoporder",
    "distancefromstop",
    "idx",
]


def get_relevant_holidays(year, month):
    date_check = datetime.date(year, month, 1)
    cur_heb_date = hdate.HebrewDate.from_jdn(hdate.converters.gdate_to_jdn(date_check))

    db = HolidayDatabase(diaspora=False)
    holiday_dict = {k: v[0].name for k, v in list(db.lookup_holidays_for_year(cur_heb_date).items())}
    holiday_dict = {
        k: v
        for k, v in holiday_dict.items()
        if v
        not in [
            "rosh_chodesh",
            "chanukah",
            "rabin_memorial_day",
            "zeev_zhabotinsky_day",
            "yom_hashoah",
            "tu_bav",
            "tisha_bav",
            "tzom_tammuz",
            "tzom_gedaliah",
            "tu_bshvat",
            "asara_btevet",
            "memorial_day_unknown",
            "yom_hazikaron",
            "taanit_esther",
            "hol_hamoed_sukkot",
            "hol_hamoed_pesach",
        ]
    }
    return holiday_dict


def check_if_holiday(year, month, day, holiday_dict):
    date_check = datetime.date(year, month, day)
    cur_heb_date = hdate.HebrewDate.from_jdn(hdate.converters.gdate_to_jdn(date_check))

    return cur_heb_date in [x for x in list(holiday_dict.keys())]


# isr_holidays = holidays.country_holidays('IL', years=year)


def generate_hourly_timestamps(times) -> List[datetime.datetime]:
    start, end = times
    timestamps = []
    current = start
    while current < end:
        timestamps.append(current)
        current += timedelta(hours=1)
    return timestamps


load_dotenv(dotenv_path="../.env")


def leave_out_for_time_period(time_period: str = "weekdays") -> List[int]:
    """Weekday indices (Mon=0..Sun=6) to exclude for a time period."""
    if time_period == "weekdays":
        return [4, 5]  # Fri, Sat
    if time_period == "weekend":
        return [0, 1, 2, 3, 6]  # keep Fri+Sat
    if time_period == "friday":
        return [0, 1, 2, 3, 5, 6]  # keep Fri only
    raise ValueError(f"time_period must be weekdays|weekend|friday, got {time_period!r}")


def get_weekdays_in_month(year, month):
    """
    Returns a list of all weekdays (Sunday-Thursday) in a given month and year.
    """
    return get_days_in_month(year, month, time_period="weekdays")


def get_days_in_month(year, month, time_period: str = "weekdays"):
    """
    Returns timestamps for days in the month matching time_period (excl. holidays).
    """
    leave_out = leave_out_for_time_period(time_period)
    holiday_dict = get_relevant_holidays(year, month)
    days = []
    num_days = calendar.monthrange(year, month)[1]
    for day in range(1, num_days + 1):
        current_date = date(year, month, day)
        if current_date.weekday() in leave_out:
            continue
        if check_if_holiday(year, month, day, holiday_dict):
            continue
        days.append(pd.Timestamp(current_date))
    return days


def create_sql_query_n_table_name(alt_num_list, year, month, day, tablename=None, named_columns: bool = False):
    """
    Build SELECT for a day table.

    named_columns=False: raw parquet import columns ("1" = lineref).
    named_columns=True: cleaned table with SIRI_COLUMNS names (lineref).
    """
    if not tablename:
        tablename = f"siri_{year}_{month}_{day}"
    if alt_num_list:
        alt_num_list_str = ", ".join(str(int(x)) for x in alt_num_list)
        lineref_col = "lineref" if named_columns else '"1"'
        sql_query = f"""SELECT * FROM {tablename} WHERE {lineref_col} IN ({alt_num_list_str})"""
    else:
        sql_query = f"""SELECT * FROM {tablename}"""
    return tablename, sql_query


def siri_to_local_duckdb(
    hours,
    day,
    month,
    year,
    dbfile,
    alt_num_list=None,
    tablename=None,
    time_period: str = "weekdays",
):
    # AWS S3 configuration
    aws_access_key_id = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY")
    bucket_name = os.getenv("BUCKET_NAME")

    if not hours:
        hours = range(0, 24)

    relevant_days = get_days_in_month(year, month, time_period=time_period)

    weekday = pd.Timestamp(year=year, month=month, day=day)
    if weekday not in relevant_days:
        return None, None
    if os.path.exists(dbfile):
        with duckdb.connect(dbfile) as con:
            tables_sql = con.sql("""SHOW TABLES;""").df()
    else:
        tables_sql = None
    tablename, sql_query = create_sql_query_n_table_name(alt_num_list, year, month, weekday.day)

    if tables_sql is not None:
        if tablename in tables_sql["name"].values:
            return "finished", None

    s3r = boto3.resource("s3")
    bucket = s3r.Bucket(bucket_name)
    files_in_bucket = list(bucket.objects.all())
    files_in_bucket_key = [x.key for x in files_in_bucket]

    blocks = [weekday + timedelta(hours=h) for h in hours]
    blocks = [x.strftime("%Y/%m/%d/%H") for x in blocks]
    days_paths = [f"national/{x}.parquet" for x in blocks]

    ok_days = [x for x in days_paths if x in files_in_bucket_key]
    if len(ok_days) == 0:
        return None, None
    ok_days = [f"s3://{bucket_name}/{x}" for x in ok_days]

    parquet_list = "', '".join(ok_days)
    parquet_list = f"'{parquet_list}'"

    os.makedirs(os.path.dirname(dbfile), exist_ok=True)
    with duckdb.connect(database=dbfile) as con:

        # load extension required for reading from s3
        con.execute("INSTALL 'httpfs';")
        con.execute("LOAD 'httpfs';")

        # Set the AWS credentials to access the S3 bucket
        con.execute("SET s3_region='eu-west-3';")
        con.execute(f"SET s3_access_key_id = '{aws_access_key_id}';")
        con.execute(f"SET s3_secret_access_key = '{aws_secret_access_key}';")

        # Execute the query to load and save the data directly to the specified DuckDB file
        query = f"""CREATE OR REPLACE TABLE {tablename} AS
                    SELECT * FROM read_parquet([{parquet_list}])
                """
        print(query)
        con.execute(query)

    with duckdb.connect(dbfile) as con:

        siri_df = con.sql(sql_query).df()

        siri_df.columns = SIRI_COLUMNS
        if alt_num_list:
            alt_ints = {int(x) for x in alt_num_list}
            siri_df = siri_df.loc[siri_df["lineref"].astype(int).isin(alt_ints)]

        siri_count = siri_df["datedvehiclejourneyref"].value_counts()

        siri_df = siri_df.loc[siri_df["datedvehiclejourneyref"].isin(siri_count.loc[siri_count >= 10].index)]

        con.register("clean_df", siri_df)
        con.execute(f"CREATE OR REPLACE TABLE {tablename} AS SELECT * FROM clean_df")

    return tablename, siri_df
    # siri_df.to_parquet('./siri_realtime_gazaSt15s.parquet')


if __name__ == "__main__":

    hours = None
    month = 8
    year = 2025
    dbfile = rf"T:\GIS\Speeds\siri-realtime\data\{year}\{str(month).zfill(2)}\siri_{year}{str(month).zfill(2)}.duckdb"
