"""Konstanten für die Integration BfS Ortsdosisleistung (ODL)."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "bfs_odl"

MANUFACTURER: Final = "Bundesamt für Strahlenschutz"
MODEL: Final = "ODL-Messsonde"
ODLINFO_URL: Final = "https://odlinfo.bfs.de/"
ATTRIBUTION: Final = "Daten: Bundesamt für Strahlenschutz (BfS), odlinfo.bfs.de"

# Datenquelle
WFS_URL: Final = "https://www.imis.bfs.de/ogc/opendata/ows"
LAYER_LATEST: Final = "opendata:odlinfo_odl_1h_latest"
LAYER_TIMESERIES: Final = "opendata:odlinfo_timeseries_odl_1h"
REQUEST_TIMEOUT: Final = 30  # Sekunden
# Griechisches My (U+03BC), wie in Home Assistant üblich
UNIT_USV_H: Final = "\u03bcSv/h"

# Daten im Config-Entry
CONF_KENN: Final = "kenn"
CONF_STATION_NAME: Final = "station_name"

# Optionen
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_THRESHOLD: Final = "threshold"
CONF_PERCENT_ABOVE_MEAN: Final = "percent_above_mean"

DEFAULT_SCAN_INTERVAL: Final = 30  # Minuten
MIN_SCAN_INTERVAL: Final = 10  # Minuten
MAX_SCAN_INTERVAL: Final = 360  # Minuten
DEFAULT_THRESHOLD: Final = 0.3  # µSv/h
DEFAULT_PERCENT_ABOVE_MEAN: Final = 50  # Prozent

# Komplette Sondenliste höchstens so oft laden (Config-Flow und Fallback)
STATION_LIST_CACHE_TTL: Final = timedelta(minutes=15)
# Zeitreihe für das 7-Tage-Mittel höchstens so oft abrufen
TIMESERIES_REFRESH_INTERVAL: Final = timedelta(hours=6)
TIMESERIES_MAX_FEATURES: Final = 200
# Gleitendes Mittel
MEAN_WINDOW: Final = timedelta(days=7)
MEAN_MIN_VALUES: Final = 24  # Mindestens ein Tag Stundenwerte

# Anzahl Sonden in der Auswahl
NEAREST_STATION_COUNT: Final = 10
SEARCH_RESULT_LIMIT: Final = 25

# Persistenter Verlauf
STORAGE_VERSION: Final = 1
STORAGE_KEY_PREFIX: Final = f"{DOMAIN}.history"
STORAGE_SAVE_DELAY: Final = 60  # Sekunden

# Status der Sonde (site_status im BfS-Datensatz)
STATUS_IN_OPERATION: Final = "in_operation"
STATUS_DEFECTIVE: Final = "defective"
STATUS_TEST_OPERATION: Final = "test_operation"
STATUS_OPTIONS: Final = [STATUS_IN_OPERATION, STATUS_DEFECTIVE, STATUS_TEST_OPERATION]
SITE_STATUS_MAP: Final[dict[int, str]] = {
    1: STATUS_IN_OPERATION,
    2: STATUS_DEFECTIVE,
    3: STATUS_TEST_OPERATION,
}
