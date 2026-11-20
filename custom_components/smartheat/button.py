"""Taste je Kreis: eine laufende Abwesenheit sofort beenden. Das Druecken feuert nur das lokale Ereignis
smartheat_abwesend_ende (mit dem Kontext des Dienstaufrufs: Nutzer oder Automation); das Add-on beendet die Abwesenheit
und meldet es dem Server. Kein Server-Aufruf aus der Integration. Ohne laufende Abwesenheit, bei nicht bereitem Add-on
oder bei einem Versionsbefund lehnt die Taste ab."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ABWESEND_ENDE_EVENT, ABWESEND_ENDE_EVENT_FIELDS, DOMAIN
from .entity import SmartHeatEntity, async_kreis_entities, pruefe_kreis_bereit

KEY = "abwesenheit_beenden"


# Push ueber den Coordinator, kein Abruf je Entity: keine Begrenzung gleichzeitiger Aufrufe.
PARALLEL_UPDATES = 0


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_kreis_entities(
        hass, entry, async_add_entities, lambda coordinator, kreis: [AbwesenheitBeendenTaste(coordinator, kreis)])


class AbwesenheitBeendenTaste(SmartHeatEntity, ButtonEntity):
    def __init__(self, coordinator, kreis: int) -> None:
        super().__init__(coordinator, "button", KEY, kreis)
        self._nummer = kreis

    def _apply(self) -> None:
        """Eine Taste hat keinen Wert aus dem Status-Ereignis."""

    def _restore(self, last: State) -> None:
        """Eine Taste hat keinen Wert zum Wiederherstellen."""

    async def async_press(self) -> None:
        pruefe_kreis_bereit(self.coordinator, self._nummer)
        if not isinstance((self.coordinator.kreis(self._nummer) or {}).get("abwesend"), dict):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="nicht_abwesend")
        werte = {"schema": self.coordinator.wunsch_schema, "kreis": self._nummer}
        self.hass.bus.async_fire(
            ABWESEND_ENDE_EVENT, {feld: werte[feld] for feld in ABWESEND_ENDE_EVENT_FIELDS}, context=self._context)
