"""二元传感器：单台 AP 的在线状态。"""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base import APEntityBase
from .coordinator import BeeconMiniCoordinator
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """为每台 AP 建立在线状态传感器；新 AP 动态补建。"""
    coordinator: BeeconMiniCoordinator = hass.data[DOMAIN][entry.entry_id]

    known_aps: set[str] = set()

    @callback
    def _add_new_aps() -> None:
        new_entities: list[BinarySensorEntity] = []
        for ap in coordinator.data.aps:
            if ap.mac in known_aps:
                continue
            known_aps.add(ap.mac)
            new_entities.append(BeeconAPOnlineSensor(coordinator, ap.mac))
            new_entities.append(BeeconAPPortPlugSensor(coordinator, ap.mac))
        if new_entities:
            async_add_entities(new_entities)

    _add_new_aps()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_aps))


class BeeconAPOnlineSensor(APEntityBase, BinarySensorEntity):
    """单台 AP 的在线状态。"""

    _attr_name = "在线状态"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: BeeconMiniCoordinator, ap_mac: str) -> None:
        super().__init__(coordinator, ap_mac)
        self._attr_unique_id = f"{ap_mac}_online"

    @property
    def is_on(self) -> bool:
        ap = self._ap
        return bool(ap and ap.online)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        ap = self._ap
        if ap is None:
            return {}
        return {
            "名称": ap.display_name,
            "mac": ap.mac,
            "ip": ap.ip,
        }


class BeeconAPPortPlugSensor(APEntityBase, BinarySensorEntity):
    """单台 AP 的端口插线状态。"""

    _attr_name = "端口插线"
    _attr_device_class = BinarySensorDeviceClass.PLUG
    _attr_icon = "mdi:ethernet-cable"
    _attr_translation_key = "ap_port_plug"

    def __init__(self, coordinator: BeeconMiniCoordinator, ap_mac: str) -> None:
        super().__init__(coordinator, ap_mac)
        self._attr_unique_id = f"{ap_mac}_port_plug"

    @property
    def is_on(self) -> bool:
        ap = self._ap
        return bool(ap and ap.port_plug)


class BeeconRoamingR24EvictionBinarySensor(ACEntityBase, BinarySensorEntity):
    """2.4G 剔除低速终端开关。"""

    _attr_name = "2.4G 剔除开关"
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_icon = "mdi:wifi-off"
    _attr_translation_key = "roaming_r24_eviction_enabled"

    def __init__(self, coordinator: BeeconMiniCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_roaming_r24_eviction_enabled"

    @property
    def is_on(self) -> bool:
        rp = self.coordinator.data.rpolicy
        return bool(rp and rp.r24_eviction_enabled)


class BeeconRoamingR5EvictionBinarySensor(ACEntityBase, BinarySensorEntity):
    """5G 剔除低速终端开关。"""

    _attr_name = "5G 剔除开关"
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_icon = "mdi:wifi-off"
    _attr_translation_key = "roaming_r5_eviction_enabled"

    def __init__(self, coordinator: BeeconMiniCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_roaming_r5_eviction_enabled"

    @property
    def is_on(self) -> bool:
        rp = self.coordinator.data.rpolicy
        return bool(rp and rp.r5_eviction_enabled)
