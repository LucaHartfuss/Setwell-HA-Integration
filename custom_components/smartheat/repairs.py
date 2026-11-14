"""Reparatur-Hinweise aus den Meldungen des Geraets: je Schluessel ein nicht behebbares Issue; bekannte Schluessel mit
eigenem Text, andere mit dem allgemeinen Geraete-Hinweis. Der Text im Issue ist vorerst der Code der Meldung (die
Uebersetzung je Code folgt); die Parameter bleiben draussen. Verschwindet ein Schluessel, verschwindet das Issue. Kein
Fix-Flow (alle Issues sind nicht behebbar); HA laedt dieses Modul als repairs-Plattform nur fuer behebbare Issues."""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import (
    AUTO_UPDATE_ISSUE_ID,
    DOMAIN,
    HINWEIS_KEYS,
    HINWEIS_SAMMEL_ID,
    HINWEIS_SAMMEL_KEY,
    HINWEISE_MAX,
    ISSUE_OHNE_VERSION,
    MELDUNG_CODE_RE,
    MELDUNG_FELD_CODE,
    MELDUNG_FELD_KEY,
    MELDUNG_FELD_STUFE,
    MELDUNG_KEY_RE,
    MELDUNG_STUFEN,
    MINDEST_INTEGRATION_RE,
    STATUS_ADDON_MEHRFACH,
    STATUS_INTEGRATION_VERALTET,
    STATUS_VERSION_VALUES,
    WATCHDOG_ISSUE_ID,
    hinweis_issue_id,
)
from .texts import klartext

_ROT = MELDUNG_STUFEN[0]
_PREFIX = "hinweis_"
GERAET_HINWEIS = "geraet_hinweis"


def _rang(meldung: dict) -> int:
    """Rang der Stufe (rot vor orange vor gelb); eine unbekannte Stufe steht zuletzt."""
    stufe = meldung.get(MELDUNG_FELD_STUFE)
    return MELDUNG_STUFEN.index(stufe) if stufe in MELDUNG_STUFEN else len(MELDUNG_STUFEN)


def _code_text(meldung: dict) -> str:
    """Der Code der Meldung als Text fuers Issue; ein Wert, der kein Code ist, ergibt keinen Text."""
    code = meldung.get(MELDUNG_FELD_CODE)
    return klartext(code) if isinstance(code, str) and MELDUNG_CODE_RE.fullmatch(code) else ""


def async_update(hass: HomeAssistant, meldungen: list[dict]) -> None:
    """Issues `hinweis_…` auf den Stand von `meldungen` bringen; Eintraege ohne Schluessel oder mit einem Schluessel,
    der keiner ist, ignorieren (die Issue-ID steht im Diagnose-Download und darf keinen freien Text tragen; ihre
    Bildung: `const.hinweis_issue_id`). Hoechstens HINWEISE_MAX Issues, rote zuerst, dann orange, dann gelbe; fuer die
    uebrigen gibt es ein Sammel-Issue mit deren Anzahl. Nur rot ist ein Fehler, sonst eine Warnung."""
    registry = ir.async_get(hass)
    gueltig: dict[str, tuple[str, dict]] = {}
    for meldung in meldungen:
        if not isinstance(meldung, dict):
            continue
        key = meldung.get(MELDUNG_FELD_KEY)
        if not isinstance(key, str) or MELDUNG_KEY_RE.fullmatch(key) is None:
            continue
        gueltig.setdefault(hinweis_issue_id(key), (key, meldung))
    # sorted ist stabil: innerhalb einer Stufe bleibt die Reihenfolge des Geraets
    ausgewaehlt = sorted(gueltig.items(), key=lambda eintrag: _rang(eintrag[1][1]))
    aktuell = set()
    for issue_id, (key, meldung) in ausgewaehlt[:HINWEISE_MAX]:
        aktuell.add(issue_id)
        severity = ir.IssueSeverity.ERROR if meldung.get(MELDUNG_FELD_STUFE) == _ROT else ir.IssueSeverity.WARNING
        ir.async_create_issue(
            hass, DOMAIN, issue_id, is_fixable=False,
            severity=severity,
            translation_key=key if key in HINWEIS_KEYS else GERAET_HINWEIS,
            translation_placeholders={"text": _code_text(meldung)},
        )
    ueberzaehlig = len(ausgewaehlt) - HINWEISE_MAX
    if ueberzaehlig > 0:
        aktuell.add(HINWEIS_SAMMEL_ID)
        ir.async_create_issue(
            hass, DOMAIN, HINWEIS_SAMMEL_ID, is_fixable=False, severity=ir.IssueSeverity.WARNING,
            translation_key=HINWEIS_SAMMEL_KEY, translation_placeholders={"anzahl": str(ueberzaehlig)},
        )
    for domain, issue_id in list(registry.issues):
        if domain == DOMAIN and issue_id.startswith(_PREFIX) and issue_id not in aktuell:
            ir.async_delete_issue(hass, DOMAIN, issue_id)


def async_set_version(hass: HomeAssistant, befund: str | None, mindest_integration: object = None) -> None:
    """Das Repair zum Versionsbefund (`integration_veraltet` oder `addon_veraltet`) setzen; `None` nimmt es zurueck. Es
    gibt hoechstens eines. Nur der Text zu `integration_veraltet` nennt die Mindestversion aus dem Anlagenfeld `mindest_integration` des Ereignisses;
    sie erscheint nur, wenn sie wie eine Version aussieht, sonst nimmt das Repair den Text ohne Version. Nicht
    behebbar, Warnung (Orange), kein Push."""
    for alt in STATUS_VERSION_VALUES:
        if alt != befund:
            ir.async_delete_issue(hass, DOMAIN, alt)
    if befund is None:
        return
    version = (mindest_integration if isinstance(mindest_integration, str)
               and MINDEST_INTEGRATION_RE.fullmatch(mindest_integration) is not None else None)
    nennt_version = befund == STATUS_INTEGRATION_VERALTET and version is not None
    ohne_version = befund == STATUS_INTEGRATION_VERALTET and version is None
    ir.async_create_issue(
        hass, DOMAIN, befund, is_fixable=False, severity=ir.IssueSeverity.WARNING,
        translation_key=befund + ISSUE_OHNE_VERSION if ohne_version else befund,
        translation_placeholders={"mindest_integration": version} if nennt_version and version else None,
    )


def async_set_watchdog(hass: HomeAssistant, befund: str | None) -> None:
    """Das Repair zum Befund des Waechters (`addon_gestoppt`, `reagiert_nicht`, `addon_fehlt` oder `addon_mehrfach`)
    setzen; `None` nimmt es zurueck. Es gibt hoechstens eines, unter einer festen ID: eskaliert der Vorfall, aendert
    sich nur der Text. Nicht behebbar, als persistent angelegt, aber die Integration raeumt es bei jedem Start ab,
    auch wenn der Befund fortbesteht; der neue Waechter legt es erst nach seinem ersten Befund neu an (nach einem
    Neustart von Home Assistant fehlt es daher 10 bis 15 Minuten). Fehler, nur `addon_mehrfach` ist eine Warnung (das
    Add-on kann dabei gesund laufen). Der Text steht nur in den Uebersetzungen, ohne Platzhalter."""
    if befund is None:
        ir.async_delete_issue(hass, DOMAIN, WATCHDOG_ISSUE_ID)
        return
    severity = ir.IssueSeverity.WARNING if befund == STATUS_ADDON_MEHRFACH else ir.IssueSeverity.ERROR
    ir.async_create_issue(
        hass, DOMAIN, WATCHDOG_ISSUE_ID, is_fixable=False, is_persistent=True, severity=severity,
        translation_key=befund,
    )


def async_set_auto_update(hass: HomeAssistant, auto_update: bool | None) -> None:
    """Das Repair "automatische Updates des Add-ons aus" setzen (`False`) oder nehmen (`True`). `None` (Schalter nicht
    lesbar) aendert nichts: ein Lesefehler ist kein Beleg fuer "an" und keiner fuer "aus". Nicht behebbar, Warnung,
    vom Kunden ignorierbar; erneutes Setzen behaelt das Ignorieren. Der Text steht nur in den Uebersetzungen, ohne
    Platzhalter."""
    if auto_update is None:
        return
    if auto_update:
        ir.async_delete_issue(hass, DOMAIN, AUTO_UPDATE_ISSUE_ID)
        return
    ir.async_create_issue(
        hass, DOMAIN, AUTO_UPDATE_ISSUE_ID, is_fixable=False, severity=ir.IssueSeverity.WARNING,
        translation_key=AUTO_UPDATE_ISSUE_ID,
    )


def async_clear(hass: HomeAssistant, *, ignoriertes_auto_update_behalten: bool = False) -> None:
    """Alle eigenen Repairs nehmen. Beim Entladen (`ignoriertes_auto_update_behalten`) bleibt ein vom Kunden
    ignoriertes Repair "automatische Updates aus" in der Registry: ein Loeschen vergaesse das Ignorieren, und der
    Hinweis stuende nach dem Neuladen wieder offen da. Ignoriert ist er unsichtbar; das naechste Lebenszeichen setzt
    oder nimmt ihn. Beim Loeschen des Eintrags geht er mit."""
    async_update(hass, [])
    async_set_version(hass, None)
    async_set_watchdog(hass, None)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, AUTO_UPDATE_ISSUE_ID)
    if not (ignoriertes_auto_update_behalten and issue is not None and issue.dismissed_version is not None):
        ir.async_delete_issue(hass, DOMAIN, AUTO_UPDATE_ISSUE_ID)
