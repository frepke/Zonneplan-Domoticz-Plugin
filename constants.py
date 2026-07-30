# -*- coding: utf-8 -*-

BASE_URL = "https://app-api.zonneplan.nl"
APP_VERSION = "5.10.1"

LOGIN_COOLDOWN_SECONDS = 3600
LOGIN_PENDING_TIMEOUT_SECONDS = 900

# ============================================================
# SCHEDULE (3x per dag)
# ============================================================
DAILY_FETCH_TIMES = [
    "00:00:00","01:00:00","02:00:00","03:00:00","04:00:00","05:00:00",
    "06:00:00","07:00:00","08:00:00","09:00:00","10:00:00","11:00:00",
    "12:00:00","13:00:00","14:00:00","15:00:00","16:00:00","17:00:00",
    "18:00:00","19:00:00","20:00:00","21:00:00","22:00:00","23:00:00",
]

# Robust window so we don't miss it if Domoticz hiccups.
DAILY_FETCH_WINDOW_SECONDS = 60

# Fixed heartbeat
HEARTBEAT_SECONDS = 30

# Forecast size for widget
FORECAST_PAST_HOURS = 2
FORECAST_FUTURE_HOURS = 38

# Units
UNIT_ELEC_INCL = 1
UNIT_ELEC_SELL_EX_TAX = 2
UNIT_GAS_INCL = 3

UNIT_STATUS = 5
UNIT_LOGIN = 6
UNIT_LASTUPDATE = 7
UNIT_FORECAST_JSON = 8

DEVICE_DEFS = [
    (UNIT_ELEC_INCL, "Actual Electricity Price", "Custom", {"Custom": "1;€ / kWh"}),
    (UNIT_ELEC_SELL_EX_TAX, "Electricity Sell Price (ex energy tax)", "Custom", {"Custom": "1;€ / kWh"}),
    (UNIT_GAS_INCL, "Actual Gas Price", "Custom", {"Custom": "1;€ / m3"}),

    (UNIT_STATUS, "Status", "Text", {}),
    (UNIT_LOGIN, "Login", "Switch", {}),
    (UNIT_LASTUPDATE, "Last Update", "Text", {}),
    (UNIT_FORECAST_JSON, "Forecast JSON", "Text", {}),
]
