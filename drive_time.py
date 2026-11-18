import pandas as pd

pd.options.display.max_columns = 500
mb_may = pd.read_csv(r"T:\Medadim\maataz_ideal\data\MB_May\MB_May.csv")
mb_may.columns

mb_may = mb_may.drop(
    columns=[
        "bh_request_violation_luz_nm",
        "bh_hatamat_rechev_kav_nm",
        "bh_request_violation_hatama_nm",
        "bh_request_violation_murshe_nm",
        "BusFleetEndDate",
        "VehicleAjustment",
        "ScheduleDetails",
        "bh_IdRequest_IdRequestItem_luz",
        "bh_request_violation_luz_cd",
        "FinanceStatusforTripReason",
        "bh_IdRequest_IdRequestItem_hatama",
        "bh_request_violation_hatama_cd",
        "bh_rechev_murshe_nm",
        "bh_IdRequest_IdRequestItem_murshe",
        "motVehicleSize",
        "VehicleType",
        "BusSize",
        "FireProofStatus",
        "Metropolin",
        "FinanceStatusforTrip",
        "DataSourceforTrip",
        "bh_request_violation_murshe_cd",
        "EventCalendarFromDate",
        "EventCalendarToDate",
        "EventCalendar",
        "LinkedPassengerCountInd",
        "PassengerCountStartTime",
        "PassengerCountStopSequence",
        "StatusDepartureArrival",
        "PassengerCount_PassengersUp",
        "Fine",
        "IFTripStartTimeBiggerTHENmotNextTrip",
        "PassangerCount_PassengersDown",
        "VehicleAuthorization",
        "StoneProofStatus",
        "BusFleetStartDate",
        "motNextTrip",
        "FeederLinesInHour",
        "eh_IdRequest",
        "eh_IdRequestItem",
    ],
    errors="ignore",
)

mb_may = mb_may.loc[(mb_may["Duration_minute"].notnull()) & (mb_may["ControlScheduleStatus"] != "אי ביצוע")]
mb_may = mb_may.loc[(mb_may["AgencyType"] == "מפעילי אוטובוסים") & (mb_may["RouteExclusivity"] != "תלמידים")]
