"""Tests für die Ausfallsicherheit des Stromnetz-Eintrags (Version 1.1.2).

Hintergrund: Seit dem 05.11.2026 liefert /frequency für aktuelle Zeiträume HTTP 404
("no content available"). Das darf weder den Eintrag blockieren noch die anderen
Quellen beeinträchtigen.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.bfs_odl.const import (
    CONF_ENABLE_FREQUENCY,
    DOMAIN,
    GRID_FREQUENCY_STORAGE_KEY,
    GRID_UNIQUE_ID,
    ISSUE_FREQUENCY_MISSING,
)
from custom_components.bfs_odl.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.bfs_odl.grid_entity import entity_source

from .test_grid import (
    FREQUENCY_URL,
    NOW,
    PUBLIC_POWER_URL,
    SIGNAL_URL,
    TOTAL_POWER_URL,
    _state,
    calls_to,
    frequency_side_effect,
    power_payload,
    signal_payload,
)

LOGGER_NAME = "custom_components.bfs_odl.grid_coordinator"
FREQUENCY_ENTITIES = (
    ("sensor", "frequency_frequency"),
    ("sensor", "frequency_deviation"),
    ("sensor", "frequency_minimum_1h"),
    ("sensor", "frequency_maximum_1h"),
    ("sensor", "frequency_data_age"),
    ("binary_sensor", "frequency_abnormal"),
)
NO_CONTENT = '{"detail":"no content available"}'


def mock_signal_and_power(aioclient_mock: AiohttpClientMocker) -> None:
    """Netzampel und Erzeugung liefern normal."""
    now = dt_util.utcnow()
    aioclient_mock.get(SIGNAL_URL, text=json.dumps(signal_payload(now)))
    aioclient_mock.get(TOTAL_POWER_URL, text=json.dumps(power_payload(now)))


def mock_frequency_404(aioclient_mock: AiohttpClientMocker) -> None:
    """/frequency liefert für aktuelle Zeiträume HTTP 404."""
    aioclient_mock.get(FREQUENCY_URL, status=404, text=NO_CONTENT)


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    hass.config.language = "de"
    await hass.config.async_set_time_zone("Europe/Berlin")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _tick(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta
) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _issue(hass: HomeAssistant) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_FREQUENCY_MISSING)


def _records(
    caplog: pytest.LogCaptureFixture, level: int, text: str
) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name == LOGGER_NAME
        and record.levelno == level
        and text in record.getMessage()
    ]


def _assert_frequency_unavailable(hass: HomeAssistant, *, except_age: bool) -> None:
    for platform, suffix in FREQUENCY_ENTITIES:
        state = _state(hass, platform, suffix)
        if except_age and suffix == "frequency_data_age":
            assert state.state != STATE_UNAVAILABLE, suffix
        else:
            assert state.state == STATE_UNAVAILABLE, suffix


# ---------------------------------------------------------------------------
# (a) Frequenz 404, Netzampel und Erzeugung liefern
# ---------------------------------------------------------------------------


async def test_frequency_404_does_not_block_setup(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    grid_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Eintrag geladen, Frequenz unavailable, alle anderen Entitäten mit Werten."""
    freezer.move_to(NOW)
    mock_signal_and_power(aioclient_mock)
    mock_frequency_404(aioclient_mock)
    await _setup(hass, grid_entry)

    assert grid_entry.state is ConfigEntryState.LOADED
    # Ohne bekannten Messwert gibt es auch kein Datenalter
    _assert_frequency_unavailable(hass, except_age=False)

    registry = er.async_get(hass)
    others = [
        e
        for e in er.async_entries_for_config_entry(registry, grid_entry.entry_id)
        if entity_source(e.unique_id) != "frequency"
    ]
    assert len(others) == 3 + 12 + 1  # Ampel 3 + Erzeugung 12 + gute Zeit
    for registry_entry in others:
        state = hass.states.get(registry_entry.entity_id)
        assert state is not None
        assert state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN), (
            registry_entry.entity_id
        )

    frequency = grid_entry.runtime_data.frequency
    assert frequency.last_update_success
    assert frequency.outage
    assert _issue(hass) is None

    diag = await async_get_config_entry_diagnostics(hass, grid_entry)
    freq_diag = diag["coordinators"]["frequency"]
    assert freq_diag["last_successful_fetch"] is None
    assert "404" in freq_diag["last_error"]
    assert freq_diag["last_error_time"] == dt_util.utcnow().isoformat()
    assert freq_diag["newest_data_point"] is None
    signal_diag = diag["coordinators"]["signal"]
    assert signal_diag["last_successful_fetch"] == dt_util.utcnow().isoformat()
    assert signal_diag["last_error"] is None
    assert signal_diag["newest_data_point"] is not None
    power_diag = diag["coordinators"]["power"]
    assert power_diag["newest_data_point"] == "2026-11-05T09:00:00+00:00"


async def test_signal_failure_does_not_block_setup(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    grid_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Auch eine ausgefallene Netzampel blockiert den Eintrag nicht mehr."""
    freezer.move_to(NOW)
    aioclient_mock.get(SIGNAL_URL, status=503, text="Service Unavailable")
    aioclient_mock.get(
        TOTAL_POWER_URL, text=json.dumps(power_payload(dt_util.utcnow()))
    )
    aioclient_mock.get(FREQUENCY_URL, side_effect=frequency_side_effect())
    await _setup(hass, grid_entry)

    assert grid_entry.state is ConfigEntryState.LOADED
    assert _state(hass, "sensor", "signal_state").state == STATE_UNAVAILABLE
    assert _state(hass, "sensor", "power_solar").state == "9.0"
    assert _state(hass, "sensor", "frequency_frequency").state != STATE_UNAVAILABLE
    assert any(
        "„signal“ beim Start nicht verfügbar" in r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING
    )

    # Die Ampel erholt sich im normalen Intervall von selbst
    aioclient_mock.clear_requests()
    mock_signal_and_power(aioclient_mock)
    aioclient_mock.get(FREQUENCY_URL, side_effect=frequency_side_effect())
    await _tick(hass, freezer, timedelta(minutes=16))
    # 10:23:30 UTC liegt im grünen Fenster der gemockten Ampel
    assert _state(hass, "sensor", "signal_state").state == "green"


# ---------------------------------------------------------------------------
# (b) Alle drei Quellen scheitern
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "frequency_mock",
    [
        {"status": 404, "text": NO_CONTENT},
        {"exc": TimeoutError()},
    ],
    ids=["frequency_404", "frequency_timeout"],
)
async def test_all_sources_fail_setup_retry(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    grid_entry: MockConfigEntry,
    frequency_mock: dict[str, Any],
) -> None:
    """Scheitern alle aktivierten Quellen, wird die Einrichtung wiederholt."""
    aioclient_mock.get(SIGNAL_URL, status=503, text="Service Unavailable")
    aioclient_mock.get(TOTAL_POWER_URL, exc=TimeoutError())
    aioclient_mock.get(PUBLIC_POWER_URL, exc=TimeoutError())
    aioclient_mock.get(FREQUENCY_URL, **frequency_mock)
    await _setup(hass, grid_entry)

    assert grid_entry.state is ConfigEntryState.SETUP_RETRY
    assert grid_entry.error_reason_translation_key == "grid_not_ready"
    errors = grid_entry.error_reason_translation_placeholders["errors"]
    for source in ("signal", "frequency", "power"):
        assert f"{source}:" in errors


# ---------------------------------------------------------------------------
# (c) Frequenz kommt zurück
# ---------------------------------------------------------------------------


async def test_frequency_returns_and_issue_disappears(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    grid_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Nach 6 h ohne Daten Repair-Issue; kommen Daten zurück, Werte und kein Issue."""
    freezer.move_to(NOW)  # 11:07:30 Ortszeit
    mock_signal_and_power(aioclient_mock)
    mock_frequency_404(aioclient_mock)
    await _setup(hass, grid_entry)
    assert grid_entry.state is ConfigEntryState.LOADED

    await _tick(hass, freezer, timedelta(hours=5, minutes=59))
    assert _issue(hass) is None

    await _tick(hass, freezer, timedelta(minutes=2))
    issue = _issue(hass)
    assert issue is not None
    assert issue.is_fixable is False
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_key == ISSUE_FREQUENCY_MISSING
    assert issue.translation_placeholders == {
        "since": "05.11.2026 11:07",
        "last_measurement": "–",
    }
    _assert_frequency_unavailable(hass, except_age=False)

    # Energy-Charts liefert wieder (mit dem üblichen Verzug von 50 Minuten)
    aioclient_mock.clear_requests()
    mock_signal_and_power(aioclient_mock)
    aioclient_mock.get(FREQUENCY_URL, side_effect=frequency_side_effect())
    await _tick(hass, freezer, timedelta(seconds=61))

    for platform, suffix in FREQUENCY_ENTITIES:
        assert _state(hass, platform, suffix).state != STATE_UNAVAILABLE, suffix
    assert float(_state(hass, "sensor", "frequency_data_age").state) == 50.0
    assert _issue(hass) is None
    assert len(_records(caplog, logging.INFO, "wieder Netzfrequenz")) == 1
    frequency = grid_entry.runtime_data.frequency
    assert not frequency.outage
    assert frequency.last_success_time == dt_util.utcnow()


# ---------------------------------------------------------------------------
# (d) Log-Warnung nur einmal pro Ausfall
# ---------------------------------------------------------------------------


async def test_warning_logged_once_per_outage(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    grid_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Eine Warnung beim Wegfall, eine Info bei der Rückkehr – nicht jede Minute."""
    freezer.move_to(NOW)
    mock_signal_and_power(aioclient_mock)
    mock_frequency_404(aioclient_mock)
    await _setup(hass, grid_entry)
    for _ in range(10):
        await _tick(hass, freezer, timedelta(seconds=61))

    assert len(calls_to(aioclient_mock, FREQUENCY_URL)) == 11
    assert len(_records(caplog, logging.WARNING, "Netzfrequenz")) == 1
    # Home Assistant selbst meldet keinen Abruffehler für die Frequenz
    assert not [
        r
        for r in caplog.records
        if r.levelno >= logging.ERROR and "bfs_odl_grid_frequency" in r.getMessage()
    ]

    # Rückkehr: genau eine Info
    aioclient_mock.clear_requests()
    mock_signal_and_power(aioclient_mock)
    aioclient_mock.get(FREQUENCY_URL, side_effect=frequency_side_effect())
    for _ in range(3):
        await _tick(hass, freezer, timedelta(seconds=61))
    assert len(_records(caplog, logging.INFO, "wieder Netzfrequenz")) == 1

    # Neuer Ausfall: wieder genau eine Warnung, sobald die Stale-Grenze erreicht ist
    aioclient_mock.clear_requests()
    mock_signal_and_power(aioclient_mock)
    mock_frequency_404(aioclient_mock)
    await _tick(hass, freezer, timedelta(hours=2))
    assert len(_records(caplog, logging.WARNING, "Netzfrequenz")) == 1
    await _tick(hass, freezer, timedelta(minutes=15))
    for _ in range(5):
        await _tick(hass, freezer, timedelta(seconds=61))
    stale_warnings = _records(caplog, logging.WARNING, "keine neuen Netzfrequenz")
    assert len(stale_warnings) == 1
    assert len(_records(caplog, logging.WARNING, "Netzfrequenz")) == 2


# ---------------------------------------------------------------------------
# Spätere 404-Antworten, Neustart, Abschalten
# ---------------------------------------------------------------------------


async def test_later_404_keeps_fresh_data_without_update_failed(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    grid_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Bis zur Stale-Grenze gültig, danach unavailable; das Datenalter läuft weiter."""
    freezer.move_to(NOW)  # neuester Messwert 09:17:30 UTC
    mock_signal_and_power(aioclient_mock)
    aioclient_mock.get(FREQUENCY_URL, side_effect=frequency_side_effect())
    await _setup(hass, grid_entry)

    aioclient_mock.clear_requests()
    mock_signal_and_power(aioclient_mock)
    mock_frequency_404(aioclient_mock)
    await _tick(hass, freezer, timedelta(hours=2))
    frequency = grid_entry.runtime_data.frequency
    assert frequency.last_update_success
    assert not frequency.outage
    for platform, suffix in FREQUENCY_ENTITIES:
        assert _state(hass, platform, suffix).state != STATE_UNAVAILABLE, suffix
    assert float(_state(hass, "sensor", "frequency_data_age").state) == 170.0

    await _tick(hass, freezer, timedelta(minutes=11))
    assert frequency.last_update_success
    assert frequency.outage
    _assert_frequency_unavailable(hass, except_age=True)
    assert float(_state(hass, "sensor", "frequency_data_age").state) == 181.0

    await _tick(hass, freezer, timedelta(minutes=10))
    assert float(_state(hass, "sensor", "frequency_data_age").state) == 191.0


async def test_restored_state_after_restart(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    aioclient_mock: AiohttpClientMocker,
    grid_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Nach einem Neustart: Datenalter aus dem letzten Messwert, Issue nach 6 h."""
    freezer.move_to(NOW)
    last = dt_util.utcnow() - timedelta(hours=7)
    hass_storage[GRID_FREQUENCY_STORAGE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": GRID_FREQUENCY_STORAGE_KEY,
        "data": {
            "latest": {
                "time": last.isoformat(),
                "frequency": 50.012,
                "minimum": 49.95,
                "maximum": 50.04,
                "count": 3601,
                "window_start": (last - timedelta(hours=1)).isoformat(),
            },
            "outage": False,
            "outage_detected_at": None,
        },
    }
    mock_signal_and_power(aioclient_mock)
    mock_frequency_404(aioclient_mock)
    await _setup(hass, grid_entry)

    assert grid_entry.state is ConfigEntryState.LOADED
    _assert_frequency_unavailable(hass, except_age=True)
    assert float(_state(hass, "sensor", "frequency_data_age").state) == 420.0
    issue = _issue(hass)
    assert issue is not None
    assert issue.translation_placeholders == {
        "since": "05.11.2026 04:07",
        "last_measurement": "05.11.2026 04:07",
    }
    assert len(_records(caplog, logging.WARNING, "seit 05.11.2026 04:07")) == 1

    # Der Ausfall wird gespeichert: nach dem nächsten Neustart keine neue Warnung
    assert await hass.config_entries.async_reload(grid_entry.entry_id)
    await hass.async_block_till_done()
    assert hass_storage[GRID_FREQUENCY_STORAGE_KEY]["data"]["outage"] is True
    assert len(_records(caplog, logging.WARNING, "Netzfrequenz")) == 1
    assert _issue(hass) is not None


async def test_disabling_frequency_removes_issue(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    aioclient_mock: AiohttpClientMocker,
    grid_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Wird die Frequenz abgeschaltet, verschwindet ein offenes Repair-Issue."""
    freezer.move_to(NOW)
    mock_signal_and_power(aioclient_mock)
    mock_frequency_404(aioclient_mock)
    await _setup(hass, grid_entry)
    await _tick(hass, freezer, timedelta(hours=6, minutes=1))
    assert _issue(hass) is not None

    hass.config_entries.async_update_entry(
        grid_entry, options={**grid_entry.options, CONF_ENABLE_FREQUENCY: False}
    )
    assert await hass.config_entries.async_reload(grid_entry.entry_id)
    await hass.async_block_till_done()
    assert grid_entry.state is ConfigEntryState.LOADED
    assert _issue(hass) is None
    assert grid_entry.runtime_data.frequency is None


async def test_removing_grid_entry_cleans_up(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    aioclient_mock: AiohttpClientMocker,
    grid_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Beim Entfernen des Eintrags werden Issue und gespeicherter Stand gelöscht."""
    freezer.move_to(NOW)
    mock_signal_and_power(aioclient_mock)
    mock_frequency_404(aioclient_mock)
    await _setup(hass, grid_entry)
    await _tick(hass, freezer, timedelta(hours=6, minutes=1))
    assert _issue(hass) is not None

    assert await hass.config_entries.async_remove(grid_entry.entry_id)
    await hass.async_block_till_done()
    assert _issue(hass) is None
    assert GRID_FREQUENCY_STORAGE_KEY not in hass_storage
    assert grid_entry.unique_id == GRID_UNIQUE_ID
