"""BeeconMini AC 实体基类：AC 主机 vs 单台 AP。"""
from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo, EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import BeeconMiniCoordinator


class ACEntityBase(CoordinatorEntity[BeeconMiniCoordinator]):
    """挂在 AC 主设备下的实体基类。"""

    entity_description: EntityDescription
    _attr_has_entity_name = True

    def __init__(self, coordinator: BeeconMiniCoordinator) -> None:
        super().__init__(coordinator)
        dev = coordinator.data.device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.config_entry.entry_id)},
            name=f"BeeconMini AC · {dev.model or 'SEED'}",
            manufacturer=MANUFACTURER,
            model=dev.model or "SEED AC",
            sw_version=dev.version or None,
            serial_number=dev.sn or None,
            configuration_url=coordinator.client.base_url,
        )


class APEntityBase(CoordinatorEntity[BeeconMiniCoordinator]):
    """挂在单台 AP 设备下的实体基类。

    AP 是动态出现的，实体通过 ``ap_mac`` 唯一标识，
    每轮刷新通过 ``coordinator.data.ap_by_mac()`` 反查当前状态。
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: BeeconMiniCoordinator, ap_mac: str) -> None:
        super().__init__(coordinator)
        self._ap_mac = ap_mac

    @property
    def _ap(self):
        return self.coordinator.data.ap_by_mac(self._ap_mac)

    @property
    def available(self) -> bool:
        return super().available and self._ap is not None

    @property
    def device_info(self) -> DeviceInfo:
        ap = self._ap
        name = ap.display_name if ap else self._ap_mac
        return DeviceInfo(
            identifiers={(DOMAIN, self._ap_mac)},
            name=f"AP · {name}",
            manufacturer=MANUFACTURER,
            model="无线 AP",
            connections={("mac", self._ap_mac)},
            via_device=(DOMAIN, self.coordinator.config_entry.entry_id),
        )
