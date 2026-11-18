"""
Filename: siri_df_raw.py

The script reads and process vm files from "Hasadna"
Author: Shira Zylbertal
Email: Shira@eliabs.com
Date: December 06, 2023
"""

import brotli
import os
import json
import pandas as pd
import arrow
from glob import glob
import multiprocessing as mp
from tqdm import tqdm
from datetime import datetime
from typing import List, Tuple, Union, Optional, Literal, get_args
from time import strftime, localtime
from shutil import rmtree

_SIRI_TYPES = Literal["hasadna", "realtime"]
CPU_USE = mp.cpu_count() - 5


def flatten_json(y):
    out = {}

    def flatten(x, name=""):
        if type(x) is dict:
            for a in x:
                flatten(x[a], name + a + "_")
        elif type(x) is list:
            i = 0

            for a in x:
                flatten(a, name + str(i) + "_")
                i += 1
        else:
            out[name[:-1]] = x

    flatten(y)
    return out


def read_br(path):
    with open(path, "r") as fd:
        data = json.loads(brotli.decompress(fd.buffer.read()))
    return data


def json_to_df(data):
    rec = data["Siri"]["ServiceDelivery"]["StopMonitoringDelivery"]
    df = pd.concat(
        [
            pd.json_normalize([flatten_json(i) for i in j["MonitoredStopVisit"]])
            for j in rec
            if "MonitoredStopVisit" in j.keys()
        ]
    )
    df.columns = [i.replace("MonitoredVehicleJourney_", "") for i in df.columns]
    for i in ["ResponseTimestamp", "ResponseMessageIdentifier", "RequestMessageRef", "Status"]:
        df[i] = data["Siri"]["ServiceDelivery"][i]
    return df


def organize_df(df):
    df = df[
        (df["FramedVehicleJourneyRef_DatedVehicleJourneyRef"] != "0")
        & (df["FramedVehicleJourneyRef_DatedVehicleJourneyRef"] != 0)
    ].copy()
    df["date"] = df["RecordedAtTime"].map(lambda x: arrow.get(x).timestamp() if not pd.isnull(x) else x)
    # weekday 0 = Monday
    # df["weekday"] = df["RecordedAtTime"].map(lambda x: arrow.get(x).weekday() if not pd.isnull(x) else x)
    df["hour"] = df["RecordedAtTime"].map(lambda x: arrow.get(x).hour if not pd.isnull(x) else x)
    df = df.drop(columns=["FramedVehicleJourneyRef_DataFrameRef"], errors="ignore")
    return df


def read_siri_file(path):
    data = read_br(path)
    df = json_to_df(data)
    if len(df) > 0:
        df = organize_df(df)
        return df
    else:
        return None


def mp_read_siri_file(br_files):
    chunksize = int(round(len(br_files) / (CPU_USE * 2)))
    with mp.Pool(processes=CPU_USE) as p:
        siri_list = list(
            tqdm(
                p.imap_unordered(read_siri_file, br_files, chunksize=chunksize),
                total=len(br_files),
            )
        )
    return siri_list


def siri_hasadna(day_path):
    if os.path.exists(rf"{day_path}/all_day.parquet"):
        siri_df = pd.read_parquet(rf"{day_path}/all_day.parquet")
    else:
        br_files = glob(rf"{day_path}/*/*.br")
        br_files.sort()
        siri_list = mp_read_siri_file(br_files)
        siri_df = pd.concat(siri_list)
        siri_df = siri_df.drop_duplicates(
            subset=[
                "RecordedAtTime",
                "LineRef",
                "OperatorRef",
                "FramedVehicleJourneyRef_DatedVehicleJourneyRef",
                "VehicleLocation_Longitude",
                "VehicleLocation_Latitude",
                "VehicleRef",
                "date",
                # "weekday",
                "hour",
                "MonitoredCall_DistanceFromStop",
            ]
        )
        siri_df.to_parquet(rf"{day_path}/all_day.parquet")
        [rmtree(x) for x in {os.path.dirname(x) for x in br_files}]

    siri_df = siri_df.dropna(subset=["VehicleLocation_Longitude", "VehicleLocation_Latitude"])
    siri_df["MonitoredCall_DistanceFromStop"] = siri_df["MonitoredCall_DistanceFromStop"].astype(int)
    siri_df = siri_df.filter(
        [
            "RecordedAtTime",
            "LineRef",
            "OperatorRef",
            "FramedVehicleJourneyRef_DatedVehicleJourneyRef",
            # "OriginAimedDepartureTime",
            "VehicleLocation_Longitude",
            "VehicleLocation_Latitude",
            "VehicleRef",
            "ResponseTimestamp",
            "date",
            # "weekday",
            "hour",
            "MonitoredCall_DistanceFromStop",
        ]
    )
    siri_df = siri_df.loc[
        (siri_df["VehicleLocation_Longitude"].astype(float) > 25)
        & (siri_df["VehicleLocation_Longitude"].astype(float) < 40)
    ]
    siri_df = siri_df.loc[
        (siri_df["VehicleLocation_Latitude"].astype(float) > 25)
        & (siri_df["VehicleLocation_Latitude"].astype(float) < 40)
    ]

    return siri_df


def siri_realtime(day_path):
    siri_df = pd.read_csv(day_path)
    siri_df = siri_df.drop(columns=["bearing", "velocity"])
    siri_df = siri_df.drop_duplicates().reset_index(drop=True)
    siri_df = siri_df.rename(
        columns={
            "lon": "VehicleLocation_Longitude",
            "lat": "VehicleLocation_Latitude",
            "journeyref": "FramedVehicleJourneyRef_DatedVehicleJourneyRef",
            "lineref": "LineRef",
            "distancefromstop": "MonitoredCall_DistanceFromStop",
            "recordedattime_epoch": "date",
        }
    )
    siri_df["LineRef"] = siri_df["LineRef"].astype(float).astype(int).astype(str)
    siri_df["FramedVehicleJourneyRef_DatedVehicleJourneyRef"] = (
        siri_df["FramedVehicleJourneyRef_DatedVehicleJourneyRef"].astype(int).astype(str)
    )
    siri_df["RecordedAtTime"] = siri_df["date"].map(datetime.fromtimestamp)
    # siri_df["RecordedAtTime"] = siri_df["RecordedAtTime"].map(strftime("%Y-%m-%dT%H:%M:%S%z"))

    # siri_df["weekday"] = siri_df["RecordedAtTime"].map(lambda x: arrow.get(x).weekday() if not pd.isnull(x) else x)
    # siri_df["hour"] = siri_df["RecordedAtTime"].map(lambda x: arrow.get(x).hour if not pd.isnull(x) else x)
    siri_df["hour"] = siri_df["RecordedAtTime"].map(lambda x: x.hour if not pd.isnull(x) else x)
    siri_df["RecordedAtTime"] = siri_df["RecordedAtTime"].map(lambda x: x.strftime("%Y-%m-%dT%H:%M:%S%z"))
    return siri_df


def siri_df(day_path, siri_type: _SIRI_TYPES):
    options = get_args(_SIRI_TYPES)
    assert siri_type in options, f"'{siri_type}' is not in {options}"

    if siri_type == "hasadna":
        siri_df = siri_hasadna(day_path)

    if siri_type == "realtime":
        siri_df = siri_realtime(day_path)

    return siri_df


# day_path = r"T:\GIS\Speeds\Hasadna\2023\06\01"

### 1 : edge list between stations ->
### 2 : for every 2 siri points get only the subset of relevant edges ->
### 3 : commit speeds on the edges between those 2 siri points.
