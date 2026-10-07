"""BeeconMini AC 实体基类：AC 主机 / AP / 终端三层设备。"""
from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, POWER_LEVELS, PORT_SPEED_MAP
from .coordinator import BeeconMiniCoordinator


class ACEntityBase(CoordinatorEntity[BeeconMiniCoordinator]):
    """挂在 AC 主设备下的实体基类。"""

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
    """挂在单台 AP 设备下的实体基类。"""

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
        model = (ap.ap_model or "无线 AP") if ap else "无线 AP"
        sw_version = ap.ap_version or None if ap else None
        hw_version = ap.ap_sn or None if ap else None
        return DeviceInfo(
            identifiers={(DOMAIN, self._ap_mac)},
            name=f"AP · {name}",
            manufacturer=MANUFACTURER,
            model=model,
            sw_version=sw_version,
            hw_version=hw_version,
            connections={("mac", self._ap_mac)},
            via_device=(DOMAIN, self.coordinator.config_entry.entry_id),
        )


class ClientEntityBase(CoordinatorEntity[BeeconMiniCoordinator]):
    """挂在终端设备下的实体基类。

    每台无线终端（stas）独立成一个 device，
    via_device 指向其当前连接的 AP。
    终端离线后 device 自动清理。
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: BeeconMiniCoordinator, sta_mac: str) -> None:
        super().__init__(coordinator)
        self._sta_mac = sta_mac

    @property
    def _sta(self):
        return self.coordinator.data.sta_by_mac(self._sta_mac)

    @property
    def available(self) -> bool:
        return super().available and self._sta is not None

    @property
    def device_info(self) -> DeviceInfo:
        sta = self._sta
        ap_via = None
        if sta and sta.ap_mac:
            ap_via = (DOMAIN, sta.ap_mac)
        name = sta.display_name if sta else self._sta_mac
        return DeviceInfo(
            identifiers={(DOMAIN, self._sta_mac)},
            name=name,
            manufacturer="BeeconMini Client",
            connections={("mac", self._sta_mac)},
            via_device=ap_via,
        )


def format_power_level(code: int) -> str:
    """功率档码转文案。"""
    if code < 0:
        return "未知"
    return POWER_LEVELS.get(code, f"档 {code}")


def format_port_speed(code: int) -> str:
    """端口速率码转文案。"""
    if code < 0 or code == 255:
        return "Auto"
    return PORT_SPEED_MAP.get(code, f"{code}")
