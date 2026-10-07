"""传感器实体：AC 主机指标 + 每台 AP 的终端数与详情。"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfInformation,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base import ACEntityBase, APEntityBase, ClientEntityBase, format_port_speed
from .coordinator import BeeconMiniCoordinator
from .const import DOMAIN
from .model import ACState
@dataclass(frozen=True, kw_only=True)
class ACSensorDescription(SensorEntityDescription):
    """AC 主机传感器描述。"""

    value_fn: Callable[[ACState], float | int | str | None]
AC_SENSORS: tuple[ACSensorDescription, ...] = (
    ACSensorDescription(
        key="cpu_temp",
        translation_key="cpu_temp",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda s: s.device.cpu_temp,
    ),
    ACSensorDescription(
        key="cpu_usage",
        translation_key="cpu_usage",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:speedometer",
        value_fn=lambda s: s.device.cpu_usage,
    ),
    ACSensorDescription(
        key="mem_usage",
        translation_key="mem_usage",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:memory",
        value_fn=lambda s: s.device.mem_usage,
    ),
    ACSensorDescription(
        key="conn_num",
        translation_key="conn_num",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:lan-connect",
        value_fn=lambda s: s.device.conn_num,
    ),
    ACSensorDescription(
        key="client_count",
        translation_key="client_count",

        icon="mdi:wifi",
        value_fn=lambda s: f"{s.wireless_client_count}/{s.wired_client_count}",
    ),
    ACSensorDescription(
        key="wan_rx",
        translation_key="wan_rx",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        value_fn=lambda s: s.wan_rx_bytes,
    ),
    ACSensorDescription(
        key="wan_tx",
        translation_key="wan_tx",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        value_fn=lambda s: s.wan_tx_bytes,
    ),
    ACSensorDescription(
        key="ap_status",
        translation_key="ap_status",

        icon="mdi:access-point",
        value_fn=lambda s: f"{sum(1 for a in s.aps if a.online)}/{len(s.aps)}" if s.aps else "0/0",
    ),
)
async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """建立 AC 主机传感器 + 动态 AP 传感器。"""
    coordinator: BeeconMiniCoordinator = hass.data[DOMAIN][entry.entry_id]

    # AC 主机指标
    async_add_entities([BeeconACSensor(coordinator, d) for d in AC_SENSORS])

    # 动态 AP 传感器
    known_aps: set[str] = set()

    @callback
    def _add_new_aps() -> None:
        new_entities: list[SensorEntity] = []
        for ap in coordinator.data.aps:
            if ap.mac in known_aps:
                continue
            known_aps.add(ap.mac)
            new_entities.append(BeeconAPClientSensor(coordinator, ap.mac))
            new_entities.append(BeeconAPChannel24Sensor(coordinator, ap.mac))
            new_entities.append(BeeconAPChannel5Sensor(coordinator, ap.mac))
            new_entities.append(BeeconAPPortSpeedSensor(coordinator, ap.mac))
            new_entities.append(BeeconAPUptimeSensor(coordinator, ap.mac))
        if new_entities:
            async_add_entities(new_entities)

    _add_new_aps()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_aps))

    # 动态终端 RSSI 传感器
    known_stas: set[str] = set()

    @callback
    def _add_new_clients() -> None:
        new_entities: list[SensorEntity] = []
        for sta in coordinator.data.stas:
            if sta.mac in known_stas:
                continue
            known_stas.add(sta.mac)
            new_entities.append(BeeconClientRSSISensor(coordinator, sta.mac))
        if new_entities:
            async_add_entities(new_entities)

    _add_new_clients()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_clients))
class BeeconACSensor(ACEntityBase, SensorEntity):
    """AC 主机传感器。"""

    entity_description: ACSensorDescription

    def __init__(self, coordinator: BeeconMiniCoordinator, description: ACSensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{description.key}"
        name_map = {
            "cpu_temp": "CPU 温度",
            "cpu_usage": "CPU 占用",
            "mem_usage": "内存占用",
            "conn_num": "连接数",
            "client_count": "终端数",
            "wan_rx": "WAN 接收流量",
            "wan_tx": "WAN 发送流量",
            "ap_status": "AP 状态",
        }
        self._attr_name = name_map.get(description.key, description.key)

    @property
    def native_value(self) -> float | int | str | None:
        return self.entity_description.value_fn(self.coordinator.data)
class BeeconAPClientSensor(APEntityBase, SensorEntity):
    """单台 AP 的接入终端数。"""

    _attr_name = "接入终端数"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:account-multiple"
    _attr_translation_key = "ap_clients"

    def __init__(self, coordinator: BeeconMiniCoordinator, ap_mac: str) -> None:
        super().__init__(coordinator, ap_mac)
        self._attr_unique_id = f"{ap_mac}_clients"

    @property
    def native_value(self) -> int | None:
        ap = self._ap
        return ap.total_clients if ap else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        ap = self._ap
        if ap is None:
            return {}
        return {
            "mac": ap.mac,
            "ip": ap.ap_ip or ap.ip,
            "有线终端": ap.wired_clients,
            "无线终端": ap.wireless_clients,
            "已纳管": ap.configured,
            "射频数": ap.radios,
            "负载指标": ap.load,
        }
class BeeconAPChannel24Sensor(APEntityBase, SensorEntity):
    """单台 AP 的 2.4G 终端数。"""

    _attr_name = "2.4G 终端数"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:wifi-24ghz"
    _attr_translation_key = "ap_clients_24"

    def __init__(self, coordinator: BeeconMiniCoordinator, ap_mac: str) -> None:
        super().__init__(coordinator, ap_mac)
        self._attr_unique_id = f"{ap_mac}_clients_24"

    @property
    def native_value(self) -> int | None:
        ap = self._ap
        return ap.users_24 if ap else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        ap = self._ap
        if ap is None:
            return {}
        return {
            "信道": ap.channel_24,
            "功率档": ap.power_24_code,
        }
class BeeconAPChannel5Sensor(APEntityBase, SensorEntity):
    """单台 AP 的 5G 终端数。"""

    _attr_name = "5G 终端数"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:wifi-5ghz"
    _attr_translation_key = "ap_clients_5"

    def __init__(self, coordinator: BeeconMiniCoordinator, ap_mac: str) -> None:
        super().__init__(coordinator, ap_mac)
        self._attr_unique_id = f"{ap_mac}_clients_5"

    @property
    def native_value(self) -> int | None:
        ap = self._ap
        return ap.users_5 if ap else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        ap = self._ap
        if ap is None:
            return {}
        return {
            "信道": ap.channel_5,
            "功率档": ap.power_5_code,
        }
class BeeconAPPortSpeedSensor(APEntityBase, SensorEntity):
    """单台 AP 的端口协商速率。"""

    _attr_name = "端口速率"
    _attr_icon = "mdi:ethernet-cable"
    _attr_translation_key = "ap_port_speed"

    def __init__(self, coordinator: BeeconMiniCoordinator, ap_mac: str) -> None:
        super().__init__(coordinator, ap_mac)
        self._attr_unique_id = f"{ap_mac}_port_speed"

    @property
    def native_value(self) -> str | None:
        ap = self._ap
        if ap is None:
            return None
        return format_port_speed(ap.port_speed_code)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        ap = self._ap
        if ap is None:
            return {}
        return {
            "协商速率": format_port_speed(ap.port_speed_code),
            "端口能力": format_port_speed(ap.port_cap_code),
            "端口插线": "未知" if ap.port_plug is None else ("已连接" if ap.port_plug else "已拔出"),
        }
class BeeconAPUptimeSensor(APEntityBase, SensorEntity):
    """单台 AP 的运行时长（秒）。"""

    _attr_name = "运行时长"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = "s"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "ap_uptime"

    def __init__(self, coordinator: BeeconMiniCoordinator, ap_mac: str) -> None:
        super().__init__(coordinator, ap_mac)
        self._attr_unique_id = f"{ap_mac}_uptime"

    @property
    def native_value(self) -> int | None:
        ap = self._ap
        return ap.uptime_seconds if ap else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        ap = self._ap
        if ap is None:
            return {}
        uptime = ap.uptime_seconds
        days = uptime // 86400
        hours = (uptime % 86400) // 3600
        minutes = (uptime % 3600) // 60
        return {
            "运行天数": days,
            "运行小时": hours,
            "运行分钟": minutes,
        }
class BeeconClientRSSISensor(ClientEntityBase, SensorEntity):
    """无线终端的 RSSI 信号强度传感器。"""

    _attr_name = "信号强度"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = "dBm"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "rssi"

    def __init__(self, coordinator: BeeconMiniCoordinator, sta_mac: str) -> None:
        super().__init__(coordinator, sta_mac)
        self._attr_unique_id = f"{sta_mac}_rssi"

    @property
    def native_value(self) -> int | None:
        return self._sta.rssi if self._sta else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        sta = self._sta
        if sta is None:
            return {}
        return {
            "名称": sta.display_name,
            "ip": sta.ip,
            "mac": sta.mac,
            "所属AP": sta.ap_name or sta.ap_mac,
            "频段": sta.band,
            "信道": sta.channel,
            "协议": sta.phy_mode,
            "Tx_Mbps": sta.tx_rate,
            "Rx_Mbps": sta.rx_rate,
            "MLO": sta.mlo,
        }
