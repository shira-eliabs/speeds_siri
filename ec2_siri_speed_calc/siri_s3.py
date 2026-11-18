"""S3 → DuckDB SIRI day extract (package-local, adapted from parent utils/duckdb_s3)."""

from __future__ import annotations

import calendar
import datetime
import os
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd

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


def leave_out_for_time_period(time_period: str = "weekdays") -> List[int]:
    if time_period == "weekdays":
        return [4, 5]
    if time_period == "weekend":
        return [0, 1, 2, 3, 6]
    if time_period == "friday":
        return [0, 1, 2, 3, 5, 6]
    raise ValueError(time_period)


def _holiday_set(year: int, month: int) -> set:
    try:
        import hdate
        from hdate.holidays import HolidayDatabase

        date_check = datetime.date(year, month, 1)
        cur_heb_date = hdate.HebrewDate.from_jdn(hdate.converters.gdate_to_jdn(date_check))
        db = HolidayDatabase(diaspora=False)
        holiday_dict = {k: v[0].name for k, v in list(db.lookup_holidays_for_year(cur_heb_date).items())}
        skip = {
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
        }
        return {k for k, v in holiday_dict.items() if v not in skip}
    except Exception:
        return set()


def get_days_in_month(year, month, time_period: str = "weekdays"):
    leave_out = leave_out_for_time_period(time_period)
    holidays = _holiday_set(year, month)
    days = []
    num_days = calendar.monthrange(year, month)[1]
    for day in range(1, num_days + 1):
        current_date = date(year, month, day)
        if current_date.weekday() in leave_out:
            continue
        try:
            import hdate

            cur_heb = hdate.HebrewDate.from_jdn(hdate.converters.gdate_to_jdn(current_date))
            if cur_heb in holidays:
                continue
        except Exception:
            pass
        days.append(pd.Timestamp(current_date))
    return days


def create_sql_query_n_table_name(alt_num_list, year, month, day, tablename=None):
    if not tablename:
        tablename = f"siri_{year}_{month}_{day}"
    if alt_num_list:
        alt_num_list_str = ", ".join(str(int(x)) for x in alt_num_list)
        sql_query = f"""SELECT * FROM {tablename} WHERE lineref IN ({alt_num_list_str})"""
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
    time_period: str = "weekdays",
) -> Tuple[Optional[str], Optional[pd.DataFrame]]:
    """Pull national SIRI hour parquets from S3 into DuckDB; return table name + df."""
    try:
        import duckdb
        import boto3
        from dotenv import load_dotenv, find_dotenv

        load_dotenv(find_dotenv())
    except ImportError as e:
        raise ImportError(
            f"siri_s3 requires duckdb, boto3, python-dotenv ({e}). Install on the EC2 image."
        ) from e

    aws_access_key_id = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY")
    bucket_name = os.getenv("BUCKET_NAME")
    if not bucket_name:
        raise EnvironmentError("BUCKET_NAME not set")

    if not hours:
        hours = range(0, 24)

    relevant_days = get_days_in_month(year, month, time_period=time_period)
    weekday = pd.Timestamp(year=year, month=month, day=day)
    if weekday not in relevant_days:
        return None, None

    tablename, sql_query = create_sql_query_n_table_name(alt_num_list, year, month, weekday.day)

    if os.path.exists(dbfile):
        with duckdb.connect(dbfile) as con:
            tables_sql = con.sql("SHOW TABLES;").df()
        if tablename in tables_sql["name"].values:
            return "finished", None

    s3r = boto3.resource("s3")
    bucket = s3r.Bucket(bucket_name)
    files_in_bucket_key = [x.key for x in bucket.objects.all()]

    blocks = [weekday + timedelta(hours=h) for h in hours]
    blocks = [x.strftime("%Y/%m/%d/%H") for x in blocks]
    days_paths = [f"national/{x}.parquet" for x in blocks]
    ok_days = [f"s3://{bucket_name}/{x}" for x in days_paths if x in files_in_bucket_key]
    if not ok_days:
        return None, None

    parquet_list = "', '".join(ok_days)
    parquet_list = f"'{parquet_list}'"
    os.makedirs(os.path.dirname(dbfile) or ".", exist_ok=True)

    with duckdb.connect(database=dbfile) as con:
        con.execute("INSTALL 'httpfs';")
        con.execute("LOAD 'httpfs';")
        con.execute("SET s3_region='eu-west-3';")
        con.execute(f"SET s3_access_key_id = '{aws_access_key_id}';")
        con.execute(f"SET s3_secret_access_key = '{aws_secret_access_key}';")
        query = f"""CREATE OR REPLACE TABLE {tablename} AS
                    SELECT * FROM read_parquet([{parquet_list}])
                """
        print(query, flush=True)
        con.execute(query)

    with duckdb.connect(dbfile) as con:
        siri_df = con.sql(sql_query).df()
        # Normalize columns if raw parquet ordinal names
        if list(siri_df.columns)[:3] != SIRI_COLUMNS[:3] and len(siri_df.columns) >= len(SIRI_COLUMNS):
            try:
                siri_df.columns = SIRI_COLUMNS
            except Exception:
                pass
        if alt_num_list:
            alt_ints = {int(x) for x in alt_num_list}
            siri_df = siri_df.loc[siri_df["lineref"].astype(int).isin(alt_ints)]
        siri_count = siri_df["datedvehiclejourneyref"].value_counts()
        siri_df = siri_df.loc[siri_df["datedvehiclejourneyref"].isin(siri_count.loc[siri_count >= 10].index)]
        con.register("clean_df", siri_df)
        con.execute(f"CREATE OR REPLACE TABLE {tablename} AS SELECT * FROM clean_df")

    return tablename, siri_df


def read_siri_day(dbfile: str, year: int, month: int, day: int, alt_num_list=None):
    """Read cleaned day table from DuckDB into GeoDataFrame-ready frame."""
    import duckdb
    from geometry_tools import lon_lat_to_geom

    tablename, sql_query = create_sql_query_n_table_name(alt_num_list, year, month, day)
    with duckdb.connect(dbfile) as con:
        siri_df = con.sql(sql_query).df()
    if "recordedattime" not in siri_df.columns and "1" in siri_df.columns:
        siri_df.columns = SIRI_COLUMNS
    siri_df["hour"] = pd.to_datetime(siri_df["recordedattime"]).dt.hour
    siri_df["start_hour"] = pd.to_datetime(siri_df["originaimeddeparturetime"]).dt.hour
    siri_df = lon_lat_to_geom(siri_df, "longitude", "latitude")
    siri_df = siri_df.rename(
        columns={
            "lineref": "ALT_NUM",
            "distancefromstop": "MonitoredCall_DistanceFromStop",
            "datedvehiclejourneyref": "FramedVehicleJourneyRef_DatedVehicleJourneyRef",
            "recordedattime": "RecordedAtTime",
        }
    )
    siri_df["ALT_NUM"] = siri_df["ALT_NUM"].astype(str)
    siri_df["RecordedAtTime"] = pd.to_datetime(siri_df["RecordedAtTime"])
    return siri_df
