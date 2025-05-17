"""The openwbmqtt component for controlling the openWB wallbox via home assistant / MQTT."""
from __future__ import annotations

import copy
import logging

from homeassistant.components.mqtt import async_publish, async_subscribe
from homeassistant.components.switch import DOMAIN, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import slugify

from .common import OpenWBBaseEntity
from .const import (
    CHARGE_POINTS,
    MQTT_ROOT_TOPIC,
    SWITCHES_PER_LP,
    openwbSwitchEntityDescription,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Return switch entities."""
    integrationUniqueID = config_entry.unique_id
    mqttRoot = config_entry.data[MQTT_ROOT_TOPIC]
    nChargePoints = config_entry.data[CHARGE_POINTS]

    switchList = []

    # todo: global switches

    for chargePoint in range(1, nChargePoints + 1):
        localSwitchesPerLP = copy.deepcopy(SWITCHES_PER_LP)
        for description in localSwitchesPerLP:
            if description.mqttTopicChargeMode:
                description.mqttTopicCommand = f"{mqttRoot}/config/set/{str(description.mqttTopicChargeMode)}/lp/{str(chargePoint)}/{description.mqttTopicCommand}"
                description.mqttTopicCurrentValue = f"{mqttRoot}/config/get/{str(description.mqttTopicChargeMode)}/lp/{str(chargePoint)}/{description.mqttTopicCurrentValue}"
            else:  # for manual SoC module
                description.mqttTopicCommand = f"{mqttRoot}/set/lp/{str(chargePoint)}/{description.mqttTopicCommand}"
                description.mqttTopicCurrentValue = f"{mqttRoot}/lp/{str(chargePoint)}/{description.mqttTopicCurrentValue}"
            switchList.append(
                openwbSwitch(
                    unique_id=integrationUniqueID,
                    description=description,
                    nChargePoints=int(nChargePoints),
                    currentChargePoint=chargePoint,
                    device_friendly_name=integrationUniqueID,
                    mqtt_root=mqttRoot,
                )
            )

    async_add_entities(switchList)


class openwbSwitch(OpenWBBaseEntity, SwitchEntity):
    """Entity representing the inverter operation mode."""

    entity_description: openwbSwitchEntityDescription

    def __init__(
        self,
        unique_id: str,
        device_friendly_name: str,
        description: openwbSwitchEntityDescription,
        mqtt_root: str,
        currentChargePoint: int | None = None,
        nChargePoints: int | None = None,
    ) -> None:
        """Initialize the sensor and the openWB device."""
        super().__init__(
            device_friendly_name=device_friendly_name,
            mqtt_root=mqtt_root,
        )
        # Initialize the inverter operation mode setting entity
        self.entity_description = description
        
        # Optimistischen Modus aktivieren
        self._attr_optimistic = True
        
        # Attribute explizit initialisieren
        self._attr_is_on = False
        self._attr_available = True

        if nChargePoints:
            self._attr_unique_id = slugify(
                f"{unique_id}-CP{currentChargePoint}-{description.name}"
            )
            self.entity_id = (
                f"{DOMAIN}.{unique_id}-CP{currentChargePoint}-{description.name}"
            )
            self._attr_name = f"{description.name} (LP{currentChargePoint})"
        else:
            self._attr_unique_id = slugify(f"{unique_id}-{description.name}")
            self.entity_id = f"{DOMAIN}.{unique_id}-{description.name}"
            self._attr_name = description.name
            
        _LOGGER.debug("Initializing switch %s with command topic %s and state topic %s", 
                     self._attr_name, description.mqttTopicCommand, description.mqttTopicCurrentValue)

    async def async_added_to_hass(self):
        """Subscribe to MQTT events."""

        @callback
        def message_received(message):
            """Handle new MQTT messages."""
            _LOGGER.debug("Received MQTT message: %s = %s", 
                         self.entity_description.mqttTopicCurrentValue, message.payload)
            
            try:
                payload_value = int(message.payload)
                if payload_value == 1:
                    self._attr_is_on = True
                elif payload_value == 0:
                    self._attr_is_on = False
                else:
                    self._attr_is_on = None
                
                self._attr_available = True
                self.async_write_ha_state()
            except Exception as e:
                _LOGGER.error("Error processing MQTT message: %s", str(e))

        # Explizit auf MQTT-Thema abonnieren
        try:
            await async_subscribe(
                self.hass,
                self.entity_description.mqttTopicCurrentValue,
                message_received,
                qos=1,
            )
            _LOGGER.debug("Successfully subscribed to MQTT topic: %s", 
                         self.entity_description.mqttTopicCurrentValue)
        except Exception as e:
            _LOGGER.error("Failed to subscribe to MQTT topic: %s", str(e))
            self._attr_available = False
            self.async_write_ha_state()

    async def async_turn_on(self, **kwargs):
        """Turn the switch on."""
        _LOGGER.debug("Turn on called for %s", self._attr_name)
        
        # Zustand vor MQTT-Operationen setzen (optimistisch)
        self._attr_is_on = True
        self.async_write_ha_state()
        
        # MQTT-Nachricht senden
        try:
            # Diese Methode entspricht genau der funktionierenden MQTT-Switch-Implementierung
            service_data = {
                "topic": self.entity_description.mqttTopicCommand,
                "payload": "1",  # String wie im externen Switch
                "qos": 1,
                "retain": False
            }
            await self.hass.services.async_call("mqtt", "publish", service_data)
            _LOGGER.debug("Successfully published ON command to %s", 
                         self.entity_description.mqttTopicCommand)
        except Exception as e:
            _LOGGER.error("Failed to publish MQTT message: %s", str(e))
            # Selbst bei Fehlern bleibt der Zustand optimistisch

    async def async_turn_off(self, **kwargs):
        """Turn the device off."""
        _LOGGER.debug("Turn off called for %s", self._attr_name)
        
        # Zustand vor MQTT-Operationen setzen (optimistisch)
        self._attr_is_on = False
        self.async_write_ha_state()
        
        # MQTT-Nachricht senden
        try:
            # Diese Methode entspricht genau der funktionierenden MQTT-Switch-Implementierung
            service_data = {
                "topic": self.entity_description.mqttTopicCommand,
                "payload": "0",  # String wie im externen Switch
                "qos": 1,
                "retain": False
            }
            await self.hass.services.async_call("mqtt", "publish", service_data)
            _LOGGER.debug("Successfully published OFF command to %s", 
                         self.entity_description.mqttTopicCommand)
        except Exception as e:
            _LOGGER.error("Failed to publish MQTT message: %s", str(e))
            # Selbst bei Fehlern bleibt der Zustand optimistisch