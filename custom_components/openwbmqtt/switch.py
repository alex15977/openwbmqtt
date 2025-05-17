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
        
        # Aktiviere optimistischen Modus
        self._attr_optimistic = True
        
        # Definiere die MQTT-Themen explizit
        self._topic_command = None
        self._topic_state = None

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
            
        # MQTT-Themen explizit speichern
        self._topic_command = description.mqttTopicCommand
        self._topic_state = description.mqttTopicCurrentValue
        
        _LOGGER.debug("Initializing switch %s with command topic %s and state topic %s", 
                     self._attr_name, self._topic_command, self._topic_state)

    async def async_added_to_hass(self):
        """Subscribe to MQTT events."""

        @callback
        def message_received(message):
            _LOGGER.debug("Received MQTT message on %s: %s", self._topic_state, message.payload)
            try:
                payload_value = int(message.payload)
                if payload_value == 1:
                    self._attr_is_on = True
                elif payload_value == 0:
                    self._attr_is_on = False
                else:
                    self._attr_is_on = None
                
                _LOGGER.debug("Updated state for %s to %s", self._attr_name, self._attr_is_on)
                self.async_write_ha_state()
            except Exception as e:
                _LOGGER.error("Error processing MQTT message: %s", str(e))

        # Subscribe to MQTT topic and connect callback message
        try:
            _LOGGER.debug("Subscribing to MQTT topic: %s", self._topic_state)
            await async_subscribe(
                self.hass,
                self._topic_state,
                message_received,
                1,
            )
            _LOGGER.debug("Successfully subscribed to MQTT topic")
        except Exception as e:
            _LOGGER.error("Failed to subscribe to MQTT topic: %s", str(e))

    async def turn_on(self, **kwargs):
        """Turn the switch on."""
        _LOGGER.debug("Turn on called for %s", self._attr_name)
        self._attr_is_on = True
        await self.publishToMQTT()
        self.async_write_ha_state()  # Direkt den Status aktualisieren

    async def turn_off(self, **kwargs):
        """Turn the device off."""
        _LOGGER.debug("Turn off called for %s", self._attr_name)
        self._attr_is_on = False
        await self.publishToMQTT()
        self.async_write_ha_state()  # Direkt den Status aktualisieren

    async def publishToMQTT(self):
        """Publish data to MQTT."""
        if not self._topic_command:
            _LOGGER.error("Cannot publish: Command topic is not set")
            return
            
        payload = str(int(self._attr_is_on))
        _LOGGER.debug("Publishing via service to topic %s: payload %s", self._topic_command, payload)
        
        try:
            # Versuch 1: Service-Aufruf
            service_data = {
                "topic": self._topic_command,
                "payload": payload,
                "qos": 1,
                "retain": False
            }
            await self.hass.services.async_call("mqtt", "publish", service_data)
            _LOGGER.debug("MQTT publish via service completed")
            
            # Versuch 2: Als Backup zusätzlich direkt über async_publish
            try:
                await async_publish(
                    self.hass, 
                    self._topic_command, 
                    payload, 
                    qos=1, 
                    retain=False
                )
                _LOGGER.debug("MQTT publish via async_publish also completed")
            except Exception as e:
                _LOGGER.warning("Backup async_publish failed, but service call should work: %s", str(e))
                
        except Exception as e:
            _LOGGER.error("All MQTT publish attempts failed: %s", str(e))