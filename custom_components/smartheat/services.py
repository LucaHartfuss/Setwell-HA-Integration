"""Dienst `abwesend_bis`: eine Abwesenheit fuer einen oder mehrere Kreise bis zu einem Zeitpunkt setzen. Wie die
Wunschtemperatur feuert der Dienst nur das lokale Ereignis smartheat_abwesend (mit dem Kontext des Aufrufs) je Kreis; das
Add-on prueft erneut, fuehrt aus und meldet an den Server. Kein Server-Aufruf aus der Integration. Alles wird vor dem
ersten Ereignis geprueft: lehnt ein Kreis des Aufrufs ab, bekommt keiner die Abwesenheit. Die Rueckkehrzeit steht nie im
Log, auch nicht in den Meldungen der Ablehnung."""
from __future__ import annotations

from datetime import datetime, timedelta

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from .const import ABWESEND_EVENT, ABWESEND_EVENT_FIELDS, ABWESEND_MAX_TAGE, DOMAIN, RAUM_SOLL_MAX, RAUM_SOLL_MIN
from .coordinator import SmartHeatCoordinator
from .entity import endliche_zahl, pruefe_kreis_bereit
from .geraete import kreis_nummer
from .number import auf_schritt

SERVICE_ABWESEND_BIS = "abwesend_bis"
SCHEMA = vol.Schema({
    vol.Required("device_id"): vol.All(cv.ensure_list, [cv.string]),
    vol.Required("bis"): vol.Any(str, datetime),
    vol.Optional("temperatur"): vol.Any(int, float),
})


def _ablehnen(schluessel: str) -> ServiceValidationError:
    return ServiceValidationError(translation_domain=DOMAIN, translation_key=schluessel)


def _bis(wert: str | datetime) -> datetime:
    """Der Zeitpunkt in UTC; ohne Zeitzone gilt die Zeitzone von Home Assistant. Liegt er nicht in der Zukunft oder
    mehr als ABWESEND_MAX_TAGE Tage voraus, lehnt der Dienst ab."""
    try:
        zeit = dt_util.parse_datetime(wert) if isinstance(wert, str) else wert
    except ValueError:
        zeit = None
    if zeit is None:
        raise _ablehnen("abwesend_bis_ungueltig")
    if zeit.tzinfo is None:
        zeit = zeit.replace(tzinfo=dt_util.get_default_time_zone())
    zeit = dt_util.as_utc(zeit)
    jetzt = dt_util.utcnow()
    if zeit <= jetzt:
        raise _ablehnen("abwesend_bis_vergangen")
    if zeit > jetzt + timedelta(days=ABWESEND_MAX_TAGE):
        raise _ablehnen("abwesend_bis_zu_weit")
    return zeit


def _temperatur(wert: float | None) -> float | None:
    """Ohne Angabe waehlt das Add-on; sonst im Bedienbereich des Wunschs, auf halbe Grade gerundet."""
    if wert is None:
        return None
    zahl = endliche_zahl(wert)
    if zahl is None or not RAUM_SOLL_MIN <= zahl <= RAUM_SOLL_MAX:
        raise _ablehnen("abwesend_temperatur_ungueltig")
    return auf_schritt(zahl)


def _kreise(hass: HomeAssistant, geraete_ids: list[str]) -> list[tuple[SmartHeatCoordinator, int]]:
    """Die Kreise der Geraete des Aufrufs (ohne Doppelte); das Geraet der Anlage oder ein fremdes ist kein Kreis."""
    register = dr.async_get(hass)
    kreise: list[tuple[SmartHeatCoordinator, int]] = []
    for geraet_id in dict.fromkeys(geraete_ids):
        geraet = register.async_get(geraet_id)
        eintrag = next((
            e for e in (hass.config_entries.async_get_entry(i) for i in (geraet.config_entries if geraet else ()))
            if e is not None and e.domain == DOMAIN and e.state is ConfigEntryState.LOADED), None)
        nummer = kreis_nummer(eintrag.data["tenant_id"], geraet) if eintrag and isinstance(geraet, dr.DeviceEntry) else None
        if eintrag is None or nummer is None:
            raise _ablehnen("kreis_unbekannt")
        kreise.append((eintrag.runtime_data, nummer))
    return kreise


async def _abwesend_bis(hass: HomeAssistant, call: ServiceCall) -> None:
    bis = _bis(call.data["bis"])
    temperatur = _temperatur(call.data.get("temperatur"))
    kreise = _kreise(hass, call.data["device_id"])
    for coordinator, kreis in kreise:
        pruefe_kreis_bereit(coordinator, kreis)
    for coordinator, kreis in kreise:
        werte = {"schema": coordinator.wunsch_schema, "kreis": kreis, "bis": bis.isoformat()}
        if temperatur is not None:
            werte["temp"] = temperatur
        hass.bus.async_fire(
            ABWESEND_EVENT, {feld: werte[feld] for feld in ABWESEND_EVENT_FIELDS if feld in werte}, context=call.context)


def async_register(hass: HomeAssistant) -> None:
    """Der Dienst gilt fuer die Integration, nicht je Eintrag: er wird einmal beim Laden der Integration angemeldet."""
    async def behandeln(call: ServiceCall) -> None:
        await _abwesend_bis(hass, call)

    hass.services.async_register(DOMAIN, SERVICE_ABWESEND_BIS, behandeln, schema=SCHEMA)
