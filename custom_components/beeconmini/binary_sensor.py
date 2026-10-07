"""二元传感器：AP 在线状态 + 漫游剔除策略。"""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base import ACEntityBase, APEntityBase
from .coordinator import BeeconMiniCoordinator
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """建立 AC 级漫游传感器 + 动态 AP 传感器。"""
    coordinator: BeeconMiniCoordinator = hass.data[DOMAIN][entry.entry_id]

    # AC 级：漫游剔除策略
    async_add_entities([
        BeeconRoamingR24BinarySensor(coordinator),
        BeeconRoamingR5BinarySensor(coordinator),
    ])

    # 动态 AP 传感器
    known_aps: set[str] = set()

    @callback
    def _add_new_aps() -> None:
        new_entities: list[BinarySensorEntity] = []
        for ap in coordinator.data.aps:
            if ap.mac in known_aps:
                continue
            known_aps.add(ap.mac)
            new_entities.append(BeeconAPOnlineSensor(coordinator, ap.mac))
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


class BeeconRoamingR24BinarySensor(ACEntityBase, BinarySensorEntity):
    """2.4G 终端剔除策略（含漫游触发 + 剔除阈值）。"""

    _attr_name = "2.4G 终端剔除"
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_icon = "mdi:wifi-off"
    _attr_translation_key = "roaming_r24_eviction"

    def __init__(self, coordinator: BeeconMiniCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_roaming_r24_eviction"

    @property
    def is_on(self) -> bool:
        rp = self.coordinator.data.rpolicy
        if not rp:
            return False
        trigger = rp.r24_roaming_trigger_dbm
        eviction = rp.r24_eviction_threshold_dbm
        return bool(
            (trigger is not None and trigger != 0)
            or (eviction is not None and eviction != 0)
        )

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        rp = self.coordinator.data.rpolicy
        if not rp:
            return {}
        return {
            "漫游触发阈值": f"{rp.r24_roaming_trigger_dbm} dBm" if rp.r24_roaming_trigger_dbm else "未设置",
            "剔除阈值": f"{rp.r24_eviction_threshold_dbm} dBm" if rp.r24_eviction_threshold_dbm else "未设置",
            "启用状态": rp.r24_eviction_enabled,
        }


class BeeconRoamingR5BinarySensor(ACEntityBase, BinarySensorEntity):
    """5G 终端剔除策略（含漫游触发 + 剔除阈值）。"""

    _attr_name = "5G 终端剔除"
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_icon = "mdi:wifi-off"
    _attr_translation_key = "roaming_r5_eviction"

    def __init__(self, coordinator: BeeconMiniCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_roaming_r5_eviction"

    @property
    def is_on(self) -> bool:
        rp = self.coordinator.data.rpolicy
        if not rp:
            return False
        trigger = rp.r5_roaming_trigger_dbm
        eviction = rp.r5_eviction_threshold_dbm
        return bool(
            (trigger is not None and trigger != 0)
            or (eviction is not None and eviction != 0)
        )

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        rp = self.coordinator.data.rpolicy
        if not rp:
            return {}
        return {
            "漫游触发阈值": f"{rp.r5_roaming_trigger_dbm} dBm" if rp.r5_roaming_trigger_dbm else "未设置",
            "剔除阈值": f"{rp.r5_eviction_threshold_dbm} dBm" if rp.r5_eviction_threshold_dbm else "未设置",
            "启用状态": rp.r5_eviction_enabled,
        }
