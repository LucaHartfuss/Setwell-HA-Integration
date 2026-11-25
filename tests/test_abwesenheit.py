"""Abwesenheit in HA: Dienst `abwesend_bis`, Taste `abwesenheit_beenden`, Anzeige `abwesend` und `zurueck_am`. Die Integration
prueft sofort, feuert nur lokale Ereignisse und schreibt die Rueckkehrzeit nie ins Log. Das Add-on bildet
`status_event` nach (Form `abwesend {bis, temp}` oder `null` im Kreis)."""
import logging

import pytest
from homeassistant.core import Context, State, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import mock_restore_cache

from custom_components.smartheat.const import (
    ABWESEND_ENDE_EVENT,
    ABWESEND_EVENT,
    DOMAIN,
    STATUS_EVENT,
)

from .addon_fakes import make_device_entry, status_event

TENANT = "wohnung1"
JETZT = "2026-11-13T10:00:00+00:00"
BIS = "2026-11-20T12:00:00+00:00"
ABWESEND_K1 = "binary_sensor.smartheat_k1_abwesend"
ZURUECK_K1 = "sensor.smartheat_k1_zurueck_am"
TASTE_K1 = "button.smartheat_k1_abwesenheit_beenden"


async def _setup(hass, **kreis1):
    entry = make_device_entry(hass, tenant_id=TENANT)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await _melden(hass, **kreis1)
    return entry


async def _melden(hass, **kreis1):
    hass.bus.async_fire(STATUS_EVENT, status_event(TENANT, "regelt", **kreis1))
    await hass.async_block_till_done()


def _geraet(hass, kennung):
    eintrag = hass.config_entries.async_entries(DOMAIN)[0]
    return dr.async_get(hass).async_get_device_by_identifier((DOMAIN, kennung), eintrag.entry_id)


def _geraet_id(hass, nummer):
    geraet = _geraet(hass, f"{TENANT}_k{nummer}")
    assert geraet is not None
    return geraet.id


def _lauschen(hass, name=ABWESEND_EVENT) -> list:
    ereignisse: list = []
    hass.bus.async_listen(name, callback(lambda event: ereignisse.append(event)))
    return ereignisse


async def _lehnt_ab(hass, ziel, schluessel, **felder):
    with pytest.raises(ServiceValidationError) as fehler:
        await hass.services.async_call(DOMAIN, "abwesend_bis", {"device_id": ziel, **felder}, blocking=True)
    assert (fehler.value.translation_domain, fehler.value.translation_key) == (DOMAIN, schluessel)


@pytest.fixture(autouse=True)
def _feste_zeit(freezer):
    freezer.move_to(JETZT)


# --- Dienst: gueltiger Aufruf ---

async def test_ein_gueltiger_aufruf_feuert_genau_ein_ereignis_mit_dem_kontext_des_aufrufs(hass, hass_admin_user):
    await _setup(hass)
    ereignisse = _lauschen(hass)
    kontext = Context(user_id=hass_admin_user.id)

    await hass.services.async_call(
        DOMAIN, "abwesend_bis", {"device_id": _geraet_id(hass, 1), "bis": BIS, "temperatur": 18.0}, blocking=True,
        context=kontext)
    await hass.async_block_till_done()

    assert [e.data for e in ereignisse] == [{"schema": 4, "kreis": 1, "bis": BIS, "temp": 18.0}]
    assert (ereignisse[0].context.id, ereignisse[0].context.user_id) == (kontext.id, hass_admin_user.id)


async def test_ohne_temperatur_traegt_das_ereignis_keine(hass):
    """Die Temperatur wahlt dann das Add-on (zuletzt gewaehlter Wert, sonst der Vorschlag): die Integration erfindet keine."""
    await _setup(hass)
    ereignisse = _lauschen(hass)

    await hass.services.async_call(DOMAIN, "abwesend_bis", {"device_id": _geraet_id(hass, 1), "bis": BIS}, blocking=True)
    await hass.async_block_till_done()

    assert [e.data for e in ereignisse] == [{"schema": 4, "kreis": 1, "bis": BIS}]


async def test_eine_zeit_ohne_zone_gilt_in_der_zone_von_ha_und_geht_als_utc_raus(hass):
    await _setup(hass)
    ereignisse = _lauschen(hass)
    await hass.config.async_set_time_zone("Europe/Berlin")

    await hass.services.async_call(
        DOMAIN, "abwesend_bis", {"device_id": _geraet_id(hass, 1), "bis": "2026-11-20 12:00:00"}, blocking=True)
    await hass.async_block_till_done()

    assert ereignisse[0].data["bis"] == "2026-11-20T11:00:00+00:00"


async def test_mehrere_kreise_bekommen_je_ein_ereignis(hass):
    await _setup(hass)
    ereignisse = _lauschen(hass)

    await hass.services.async_call(
        DOMAIN, "abwesend_bis", {"device_id": [_geraet_id(hass, 1), _geraet_id(hass, 2)], "bis": BIS}, blocking=True)
    await hass.async_block_till_done()

    assert sorted(e.data["kreis"] for e in ereignisse) == [1, 2]


async def test_das_schema_folgt_dem_zuletzt_gelesenen_schema_des_add_ons(hass):
    entry = await _setup(hass)
    ereignisse = _lauschen(hass)
    entry.runtime_data.wunsch_schema = 4

    await hass.services.async_call(DOMAIN, "abwesend_bis", {"device_id": _geraet_id(hass, 1), "bis": BIS}, blocking=True)
    await hass.async_block_till_done()

    assert ereignisse[0].data["schema"] == entry.runtime_data.wunsch_schema


async def test_der_dienst_gilt_fuer_die_integration_nicht_je_eintrag(hass):
    assert not hass.services.has_service(DOMAIN, "abwesend_bis")
    await _setup(hass)
    assert hass.services.has_service(DOMAIN, "abwesend_bis")


# --- Dienst: Bereiche ---

@pytest.mark.parametrize("bis", ["2026-11-13T10:00:01+00:00", "2027-11-13T10:00:00+00:00"])  # eine Sekunde / 365 Tage voraus
async def test_bis_an_den_gueltigen_raendern_geht_durch(hass, bis):
    await _setup(hass)
    ereignisse = _lauschen(hass)

    await hass.services.async_call(DOMAIN, "abwesend_bis", {"device_id": _geraet_id(hass, 1), "bis": bis}, blocking=True)
    await hass.async_block_till_done()

    assert [e.data["bis"] for e in ereignisse] == [bis]


@pytest.mark.parametrize("bis", [JETZT, "2026-11-13T09:59:59+00:00", "2020-01-01T00:00:00+00:00"])
async def test_bis_nicht_in_der_zukunft_wird_abgelehnt(hass, bis):
    await _setup(hass)
    ereignisse = _lauschen(hass)

    await _lehnt_ab(hass, _geraet_id(hass, 1), "abwesend_bis_vergangen", bis=bis)

    assert ereignisse == []


@pytest.mark.parametrize("bis", ["2027-11-13T10:00:01+00:00", "2030-01-01T00:00:00+00:00"])
async def test_bis_ueber_365_tage_wird_abgelehnt(hass, bis):
    await _setup(hass)
    ereignisse = _lauschen(hass)

    await _lehnt_ab(hass, _geraet_id(hass, 1), "abwesend_bis_zu_weit", bis=bis)

    assert ereignisse == []


@pytest.mark.parametrize(("eingabe", "gesendet"), [(15.0, 15.0), (25.0, 25.0), (22.25, 22.5), (18.2, 18.0), (18.3, 18.5)])
async def test_temperatur_im_bedienbereich_geht_auf_halbe_grade_gerundet_durch(hass, eingabe, gesendet):
    await _setup(hass)
    ereignisse = _lauschen(hass)

    await hass.services.async_call(
        DOMAIN, "abwesend_bis", {"device_id": _geraet_id(hass, 1), "bis": BIS, "temperatur": eingabe}, blocking=True)
    await hass.async_block_till_done()

    assert [e.data["temp"] for e in ereignisse] == [gesendet]


@pytest.mark.parametrize("temperatur", [14.9, 25.1, 0.0, -5.0, 30.0, float("nan"), float("inf")])
async def test_temperatur_ausserhalb_von_15_bis_25_wird_abgelehnt(hass, temperatur):
    await _setup(hass)
    ereignisse = _lauschen(hass)

    await _lehnt_ab(hass, _geraet_id(hass, 1), "abwesend_temperatur_ungueltig", bis=BIS, temperatur=temperatur)

    assert ereignisse == []


# --- Dienst: Ziel und Bereitschaft ---

async def test_ein_unbekanntes_geraet_und_das_geraet_der_anlage_sind_kein_kreis(hass):
    await _setup(hass)
    ereignisse = _lauschen(hass)
    anlage = _geraet(hass, TENANT)

    await _lehnt_ab(hass, "gibt-es-nicht", "kreis_unbekannt", bis=BIS)
    await _lehnt_ab(hass, anlage.id, "kreis_unbekannt", bis=BIS)

    assert ereignisse == []


async def test_ein_kreis_den_die_anlage_nicht_mehr_meldet_wird_abgelehnt(hass):
    entry = await _setup(hass)
    ereignisse = _lauschen(hass)
    geraet = _geraet_id(hass, 2)
    del entry.runtime_data.kreise[2]

    await _lehnt_ab(hass, geraet, "kreis_nicht_gemeldet", bis=BIS)

    assert ereignisse == []


@pytest.mark.parametrize(("watchdog", "schluessel"), [
    ("addon_gestoppt", "addon_nicht_bereit"), ("reagiert_nicht", "addon_nicht_bereit"), ("addon_fehlt", "addon_fehlt")])
async def test_ein_add_on_das_nichts_annehmen_kann_lehnt_ab_und_mit_entwarnung_geht_es_wieder(hass, watchdog, schluessel):
    entry = await _setup(hass)
    ereignisse = _lauschen(hass)
    entry.runtime_data._set_watchdog_status(watchdog)
    await hass.async_block_till_done()

    await _lehnt_ab(hass, _geraet_id(hass, 1), schluessel, bis=BIS)
    assert ereignisse == []

    entry.runtime_data._set_watchdog_status(None)
    await hass.async_block_till_done()
    await hass.services.async_call(DOMAIN, "abwesend_bis", {"device_id": _geraet_id(hass, 1), "bis": BIS}, blocking=True)
    await hass.async_block_till_done()
    assert len(ereignisse) == 1


@pytest.mark.parametrize("befund", ["integration_veraltet", "addon_veraltet"])
async def test_ein_versionsbefund_lehnt_ab(hass, befund):
    entry = await _setup(hass)
    ereignisse = _lauschen(hass)
    entry.runtime_data.version_status = befund

    await _lehnt_ab(hass, _geraet_id(hass, 1), befund, bis=BIS)

    assert ereignisse == []


async def test_ein_ungueltiger_kreis_unter_mehreren_verhindert_alle_ereignisse(hass):
    """Erst alles pruefen, dann feuern: kein Kreis bekommt die Abwesenheit, wenn ein anderer des Aufrufs abgelehnt wird."""
    entry = await _setup(hass)
    ereignisse = _lauschen(hass)
    geraete = [_geraet_id(hass, 1), _geraet_id(hass, 2)]
    del entry.runtime_data.kreise[2]

    await _lehnt_ab(hass, geraete, "kreis_nicht_gemeldet", bis=BIS)

    assert ereignisse == []


# --- Taste ---

async def test_die_taste_beendet_nur_eine_laufende_abwesenheit_und_feuert_genau_ein_ereignis(hass, hass_admin_user):
    await _setup(hass, abwesend={"bis": BIS, "temp": 18.0})
    ereignisse = _lauschen(hass, ABWESEND_ENDE_EVENT)
    kontext = Context(user_id=hass_admin_user.id)

    await hass.services.async_call("button", "press", {"entity_id": TASTE_K1}, blocking=True, context=kontext)
    await hass.async_block_till_done()

    assert [e.data for e in ereignisse] == [{"schema": 4, "kreis": 1}]
    assert (ereignisse[0].context.id, ereignisse[0].context.user_id) == (kontext.id, hass_admin_user.id)


@pytest.mark.parametrize("abwesend", [None, "fehlt"])
async def test_die_taste_ohne_laufende_abwesenheit_lehnt_mit_nicht_abwesend_ab(hass, abwesend):
    """Auch ein Add-on, das das Feld noch nicht kennt, hat kein laufendes Away."""
    await _setup(hass, **({} if abwesend == "fehlt" else {"abwesend": None}))
    ereignisse = _lauschen(hass, ABWESEND_ENDE_EVENT)

    with pytest.raises(ServiceValidationError) as fehler:
        await hass.services.async_call("button", "press", {"entity_id": TASTE_K1}, blocking=True)
    assert (fehler.value.translation_domain, fehler.value.translation_key) == (DOMAIN, "nicht_abwesend")

    assert ereignisse == []


@pytest.mark.parametrize(("watchdog", "schluessel"), [
    ("addon_gestoppt", "addon_nicht_bereit"), ("addon_fehlt", "addon_fehlt")])
async def test_die_taste_lehnt_bei_nicht_bereitem_add_on_ab(hass, watchdog, schluessel):
    from homeassistant.components.button import DATA_COMPONENT

    entry = await _setup(hass, abwesend={"bis": BIS, "temp": 18.0})
    ereignisse = _lauschen(hass, ABWESEND_ENDE_EVENT)
    entry.runtime_data._set_watchdog_status(watchdog)
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError) as fehler:
        await hass.data[DATA_COMPONENT].get_entity(TASTE_K1).async_press()
    assert (fehler.value.translation_domain, fehler.value.translation_key) == (DOMAIN, schluessel)
    assert ereignisse == []


async def test_die_taste_gehoert_zum_geraet_ihres_kreises(hass):
    entry = await _setup(hass)

    eintrag = er.async_get(hass).async_get(TASTE_K1)
    assert eintrag.unique_id == f"{TENANT}_k1_abwesenheit_beenden"
    assert eintrag.device_id == _geraet_id(hass, 1)
    assert er.async_get(hass).async_get("button.smartheat_k2_abwesenheit_beenden") is not None
    assert entry is not None


# --- Anzeige ---

async def test_abwesend_zeigt_ob_eine_abwesenheit_laeuft_und_unbekannt_bei_aelterem_add_on(hass):
    await _setup(hass, abwesend={"bis": BIS, "temp": 18.0})
    assert hass.states.get(ABWESEND_K1).state == "on"

    await _melden(hass, abwesend=None)
    assert hass.states.get(ABWESEND_K1).state == "off"

    # Ein Ereignis ohne das Feld (Add-on ohne Abwesenheit) bleibt unbekannt statt "aus".
    ereignis = status_event(TENANT, "regelt")
    ereignis["kreise"][0].pop("abwesend", None)
    hass.bus.async_fire(STATUS_EVENT, ereignis)
    await hass.async_block_till_done()
    assert hass.states.get(ABWESEND_K1).state == "unknown"


async def test_zurueck_am_zeigt_den_zeitpunkt_sonst_unbekannt(hass):
    await _setup(hass, abwesend={"bis": BIS, "temp": 18.0})
    zustand = hass.states.get(ZURUECK_K1)
    assert dt_util.parse_datetime(zustand.state) == dt_util.parse_datetime(BIS)
    assert zustand.attributes["device_class"] == "timestamp"

    await _melden(hass, abwesend=None)
    assert hass.states.get(ZURUECK_K1).state == "unknown"


@pytest.mark.parametrize("abwesend", [{"bis": None}, {"bis": "morgen"}, {"bis": "2026-11-20T12:00:00"}, {"bis": 5},
                                      {}, "ja", 7])
async def test_zurueck_am_zeigt_nie_etwas_unbrauchbares(hass, abwesend):
    """Ohne Zeitzone, kein Text, keine Zahl: unbekannt statt eines Fehlers beim Schreiben des Zustands."""
    await _setup(hass, abwesend=abwesend)
    assert hass.states.get(ZURUECK_K1).state == "unknown"


async def test_beide_anzeigen_stellen_nach_dem_neustart_den_letzten_stand_wieder_her(hass):
    entry = await _setup(hass, abwesend={"bis": BIS, "temp": 18.0})
    assert await hass.config_entries.async_unload(entry.entry_id)
    mock_restore_cache(hass, [State(ABWESEND_K1, "on"), State(ZURUECK_K1, BIS)])

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(ABWESEND_K1).state == "on"
    assert dt_util.parse_datetime(hass.states.get(ZURUECK_K1).state) == dt_util.parse_datetime(BIS)


# --- Log ---

GEHEIME_TEILE = ("2026-11-20", "T12:00", "12:00:00", "2027-11-13", "11:00:00")


@pytest.mark.parametrize("fall", ["gueltig", "vergangen", "zu_weit", "temperatur", "kreis"])
async def test_die_rueckkehrzeit_steht_nie_im_log(hass, caplog, fall):
    caplog.set_level(logging.DEBUG)
    await _setup(hass, abwesend={"bis": BIS, "temp": 18.0})
    ziel = "gibt-es-nicht" if fall == "kreis" else _geraet_id(hass, 1)
    versuche = {
        "gueltig": (None, {"bis": BIS}),
        "vergangen": ("abwesend_bis_vergangen", {"bis": "2020-02-02T12:00:00+00:00"}),
        "zu_weit": ("abwesend_bis_zu_weit", {"bis": "2027-11-13T11:00:00+00:00"}),
        "temperatur": ("abwesend_temperatur_ungueltig", {"bis": BIS, "temperatur": 40}),
        "kreis": ("kreis_unbekannt", {"bis": BIS}),
    }
    schluessel, felder = versuche[fall]
    if schluessel is None:
        await hass.services.async_call(DOMAIN, "abwesend_bis", {"device_id": ziel, **felder}, blocking=True)
    else:
        await _lehnt_ab(hass, ziel, schluessel, **felder)
    await hass.async_block_till_done()

    for teil in (*GEHEIME_TEILE, "2020-02-02", "2020"):
        assert teil not in caplog.text, teil
    assert BIS not in caplog.text and "Abwesen" not in caplog.text


async def test_die_taste_und_der_statuswechsel_schreiben_die_rueckkehrzeit_nicht_ins_log(hass, caplog):
    caplog.set_level(logging.DEBUG)
    await _setup(hass, abwesend={"bis": BIS, "temp": 18.0})
    await hass.services.async_call("button", "press", {"entity_id": TASTE_K1}, blocking=True)
    await _melden(hass, abwesend=None)
    await _melden(hass, abwesend={"bis": BIS, "temp": 18.0})
    await hass.async_block_till_done()

    for teil in (*GEHEIME_TEILE, BIS):
        assert teil not in caplog.text, teil


# --- Beschreibung des Dienstes ---

def test_die_dienstbeschreibung_und_die_uebersetzungen_sind_vollstaendig():
    import json
    from pathlib import Path

    import yaml

    paket = Path(__file__).parent.parent / "custom_components" / "smartheat"
    dienste = yaml.safe_load((paket / "services.yaml").read_text(encoding="utf-8"))
    assert set(dienste) == {"abwesend_bis"}
    beschreibung = dienste["abwesend_bis"]
    # hassfest verwirft Geraetefilter auf `target`: die Kreise kommen als Feld mit Geraeteauswahl.
    assert "target" not in beschreibung
    assert set(beschreibung["fields"]) == {"device_id", "bis", "temperatur"}
    assert beschreibung["fields"]["device_id"]["required"] is True
    assert beschreibung["fields"]["device_id"]["selector"] == {"device": {"integration": "smartheat", "multiple": True}}
    assert beschreibung["fields"]["bis"]["required"] is True
    assert "required" not in beschreibung["fields"]["temperatur"] or beschreibung["fields"]["temperatur"]["required"] is False
    for datei in ("strings.json", "translations/en.json", "translations/de.json"):
        text = json.loads((paket / datei).read_text(encoding="utf-8"))
        dienst = text["services"]["abwesend_bis"]
        assert dienst["name"] and dienst["description"], datei
        assert set(dienst["fields"]) == {"device_id", "bis", "temperatur"}, datei
        for feld in dienst["fields"].values():
            assert feld["name"] and feld["description"], datei
        for schluessel in ("abwesend_bis_vergangen", "abwesend_bis_zu_weit", "abwesend_temperatur_ungueltig",
                           "kreis_unbekannt", "nicht_abwesend"):
            assert text["exceptions"][schluessel]["message"], (datei, schluessel)
        assert text["issues"]["abwesend_abgelehnt"]["title"], datei
        assert "{text}" in text["issues"]["abwesend_abgelehnt"]["description"], datei
        assert text["entity"]["binary_sensor"]["abwesend"]["name"], datei
        assert text["entity"]["sensor"]["zurueck_am"]["name"], datei
        assert text["entity"]["button"]["abwesenheit_beenden"]["name"], datei
    icons = json.loads((paket / "icons.json").read_text(encoding="utf-8"))
    assert "abwesend" in icons["entity"]["binary_sensor"] and "zurueck_am" in icons["entity"]["sensor"]
    assert "abwesenheit_beenden" in icons["entity"]["button"]
    assert "abwesend_bis" in icons["services"]


async def test_ein_unlesbarer_zeitpunkt_wird_abgelehnt(hass):
    await _setup(hass)
    ereignisse = _lauschen(hass)

    for bis in ("irgendwann", "2026-13-45T25:00:00"):
        await _lehnt_ab(hass, _geraet_id(hass, 1), "abwesend_bis_ungueltig", bis=bis)

    assert ereignisse == []


async def test_die_abwesenheits_entities_ueberleben_ein_neuladen(hass):
    """Die Registry-Pflege beim Laden loescht jeden Eintrag, dessen Kennung sie nicht kennt."""
    entry = await _setup(hass)
    kennungen = {ABWESEND_K1: f"{TENANT}_k1_abwesend", ZURUECK_K1: f"{TENANT}_k1_zurueck_am",
                 TASTE_K1: f"{TENANT}_k1_abwesenheit_beenden"}
    register = er.async_get(hass)
    vorher = {entity_id: register.async_get(entity_id).id for entity_id in kennungen}

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    for entity_id, kennung in kennungen.items():
        eintrag = register.async_get(entity_id)
        assert eintrag is not None, entity_id
        assert (eintrag.id, eintrag.unique_id) == (vorher[entity_id], kennung)
