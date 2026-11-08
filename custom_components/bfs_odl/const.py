"""Konstanten für die Integration Umwelt & Netz (BfS / Energy-Charts)."""

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

# ---------------------------------------------------------------------------
# Art des Eintrags. Bestehende Einträge haben kein "entry_type" und sind ODL.
# ---------------------------------------------------------------------------
CONF_ENTRY_TYPE: Final = "entry_type"
ENTRY_TYPE_ODL: Final = "odl"
ENTRY_TYPE_GRID: Final = "grid"

# ---------------------------------------------------------------------------
# Stromnetz (Energy-Charts, Fraunhofer ISE)
# ---------------------------------------------------------------------------
GRID_UNIQUE_ID: Final = "energy_charts_de"
GRID_DEVICE_NAME: Final = "Stromnetz Deutschland (Energy-Charts)"
GRID_MANUFACTURER: Final = "Fraunhofer ISE"
GRID_MODEL: Final = "Energy-Charts API"
GRID_URL: Final = "https://www.energy-charts.info/"
GRID_ATTRIBUTION: Final = "Daten: Energy-Charts, Fraunhofer ISE (CC BY 4.0)"

ENERGY_CHARTS_URL: Final = "https://api.energy-charts.info"
GRID_COUNTRY: Final = "de"
# Laut API-Doku gibt es nur die Messung in Freiburg (RG Continental Europe)
FREQUENCY_REGION: Final = "DE-Freiburg"

CONF_POSTAL_CODE: Final = "postal_code"
CONF_ENABLE_SIGNAL: Final = "enable_signal"
CONF_ENABLE_FREQUENCY: Final = "enable_frequency"
CONF_ENABLE_POWER: Final = "enable_power"
CONF_SIGNAL_INTERVAL: Final = "signal_interval"
CONF_FREQUENCY_INTERVAL: Final = "frequency_interval"
CONF_POWER_INTERVAL: Final = "power_interval"
CONF_FREQUENCY_THRESHOLD: Final = "frequency_threshold"
CONF_FREQUENCY_STALE_AFTER: Final = "frequency_stale_after"

DEFAULT_SIGNAL_INTERVAL: Final = 15  # Minuten
MIN_SIGNAL_INTERVAL: Final = 5
MAX_SIGNAL_INTERVAL: Final = 240
DEFAULT_FREQUENCY_INTERVAL: Final = 60  # Sekunden
MIN_FREQUENCY_INTERVAL: Final = 30
MAX_FREQUENCY_INTERVAL: Final = 3600
DEFAULT_POWER_INTERVAL: Final = 15  # Minuten
MIN_POWER_INTERVAL: Final = 5
MAX_POWER_INTERVAL: Final = 240
DEFAULT_FREQUENCY_THRESHOLD: Final = 100  # mHz
MIN_FREQUENCY_THRESHOLD: Final = 10
MAX_FREQUENCY_THRESHOLD: Final = 1000
# Energy-Charts liefert die Frequenz mit rund 50 Minuten Verzug aus
DEFAULT_FREQUENCY_STALE_AFTER: Final = 180  # Minuten
MIN_FREQUENCY_STALE_AFTER: Final = 30
MAX_FREQUENCY_STALE_AFTER: Final = 720

NOMINAL_FREQUENCY: Final = 50.0  # Hz
# Minimum/Maximum über die letzte Stunde der vorhandenen Daten (nicht ab "jetzt")
FREQUENCY_WINDOW: Final = timedelta(hours=1)
# So viele Sekundenwerte bleiben im Speicher (gemessen ab dem neuesten Wert)
FREQUENCY_BUFFER: Final = timedelta(hours=3)
# Erster Abruf bzw. nach einem Neustart: mindestens so weit zurück
FREQUENCY_INITIAL_LOOKBACK: Final = timedelta(hours=3)
# Fehlen Frequenzdaten länger, wird ein Repair-Issue angelegt
FREQUENCY_REPAIR_AFTER: Final = timedelta(hours=6)
ISSUE_FREQUENCY_MISSING: Final = "frequency_missing"
# Letzter Frequenzwert und Ausfallzustand überleben einen Neustart
GRID_FREQUENCY_STORAGE_KEY: Final = f"{DOMAIN}.grid_frequency"
FREQUENCY_STORE_SAVE_DELAY: Final = 300  # Sekunden
# Erzeugungswerte erscheinen oft mit Verzug; so weit wird zurückgeschaut
POWER_LOOKBACK: Final = timedelta(hours=24)
SIGNAL_FORECAST_HOURS: Final = 24

# Netzampel: Werte laut Energy-Charts-Doku
SIGNAL_GRID_CONGESTION: Final = "grid_congestion"  # -1: Rot (Netzengpass)
SIGNAL_RED: Final = "red"  # 0: Rot (geringer Anteil Erneuerbare)
SIGNAL_YELLOW: Final = "yellow"  # 1: Gelb (durchschnittlicher Anteil)
SIGNAL_GREEN: Final = "green"  # 2: Grün (hoher Anteil Erneuerbare)
SIGNAL_MAP: Final[dict[int, str]] = {
    -1: SIGNAL_GRID_CONGESTION,
    0: SIGNAL_RED,
    1: SIGNAL_YELLOW,
    2: SIGNAL_GREEN,
}
SIGNAL_OPTIONS: Final = [
    SIGNAL_GREEN,
    SIGNAL_YELLOW,
    SIGNAL_RED,
    SIGNAL_GRID_CONGESTION,
]
SIGNAL_BEST: Final = 2
