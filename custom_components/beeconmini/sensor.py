"""传感器实体：AC 主机指标 + 每台 AP 的指标 + 每台无线终端的详情。

三层设备层级与实体归属：

* **AC 主机** —— 只放总数与主机自身指标（CPU / 内存 / 连接数 / 流量 / 终端数）；
* **每台 AP** —— 独立 device，``via_device`` 指向 AC；信道 / 功率 / 无线终端数 /
  端口速率 / 运行时长 / IP / 型号；
* **每台无线终端** —— 独立 device，``via_device`` 指向**所属 AP**，
  于是 AP 设备页会由 HA 原生渲染出「已连接的设备」卡片，
  点进任意一台终端即是它的详情页（8 个传感器 + 控制区剔除按钮）。
"""
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
    UnitOfDataRate,
    UnitOfInformation,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import (
    ACState,
    BeeconAP,
    BeeconMiniCoordinator,
    BeeconSta,
    format_port_speed,
    format_power_level,
    mlo_attrs,
    sta_summary,
    text_or_none,
)
from .base import ACEntityBase, APEntityBase, ClientEntityBase
from .const import DOMAIN

# ----------------------------------------------------------------------
# AC 主机传感器（描述表驱动）
# ----------------------------------------------------------------------
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

# ----------------------------------------------------------------------
# AP 传感器描述表（7 合 1）
# ----------------------------------------------------------------------
@dataclass(frozen=True, kw_only=True)
class APSensorDescription(SensorEntityDescription):
    """AP 传感器描述。``attrs_fn`` 接收 ``(ap, coord)`` 双参数。"""

    value_fn: Callable[[BeeconAP], float | int | str | None]
    attrs_fn: Callable[[BeeconAP, BeeconMiniCoordinator], dict[str, object]] = lambda a, c: {}


def _ap_channel_text(ap: BeeconAP) -> str:
    c24 = str(ap.channel_24) if ap.channel_24 else "?"
    c5 = str(ap.channel_5) if ap.channel_5 else "?"
    return f"2.4G:{c24} 5G:{c5}"


def _ap_power_text(ap: BeeconAP) -> str:
    p24 = format_power_level(ap.power_24_code) if ap.power_24_code is not None else "?"
    p5 = format_power_level(ap.power_5_code) if ap.power_5_code is not None else "?"
    return f"2.4G:{p24} 5G:{p5}"


def _attr_channel(ap: BeeconAP, coord: BeeconMiniCoordinator) -> dict[str, object]:
    return {"2.4G 信道": ap.channel_24, "5G 信道": ap.channel_5}


def _attr_power(ap: BeeconAP, coord: BeeconMiniCoordinator) -> dict[str, object]:
    return {
        "2.4G 功率": format_power_level(ap.power_24_code) if ap.power_24_code is not None else "未知",
        "5G 功率": format_power_level(ap.power_5_code) if ap.power_5_code is not None else "未知",
    }


def _attr_clients(ap: BeeconAP, coord: BeeconMiniCoordinator) -> dict[str, object]:
    stas = coord.data.stas_of_ap(ap.mac)
    return {
        "2.4G 终端": ap.users_24,
        "5G 终端": ap.users_5,
        "AP 自报总数": ap.ap_reported_count,
        "终端列表": [sta_summary(s) for s in stas],
        "射频数": ap.radios,
    }


def _attr_port_speed(ap: BeeconAP, coord: BeeconMiniCoordinator) -> dict[str, object]:
    return {
        "协商速率": format_port_speed(ap.port_speed_code),
        "端口能力": format_port_speed(ap.port_cap_code),
        "端口插线": "未知" if ap.port_plug is None else ("已连接" if ap.port_plug else "已拔出"),
    }


def _attr_uptime(ap: BeeconAP, coord: BeeconMiniCoordinator) -> dict[str, object]:
    uptime = ap.uptime_seconds
    return {
        "运行天数": uptime // 86400,
        "运行小时": (uptime % 86400) // 3600,
        "运行分钟": (uptime % 3600) // 60,
    }


AP_SENSORS: tuple[APSensorDescription, ...] = (
    APSensorDescription(
        key="channel",
        name="信道",
        translation_key="ap_channel",
        icon="mdi:wifi",
        value_fn=_ap_channel_text,
        attrs_fn=_attr_channel,
    ),
    APSensorDescription(
        key="power",
        name="功率",
        translation_key="ap_power",
        icon="mdi:signal-cellular-2",
        value_fn=_ap_power_text,
        attrs_fn=_attr_power,
    ),
    APSensorDescription(
        key="clients",
        name="无线终端数",
        translation_key="ap_clients",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:account-multiple",
        value_fn=lambda a: a.sta_count,
        attrs_fn=_attr_clients,
    ),
    APSensorDescription(
        key="port_speed",
        name="端口速率",
        translation_key="ap_port_speed",
        icon="mdi:ethernet-cable",
        value_fn=lambda a: a.port_speed_text,
        attrs_fn=_attr_port_speed,
    ),
    APSensorDescription(
        key="uptime",
        name="运行时长",
        translation_key="ap_uptime",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement="s",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda a: a.uptime_seconds or None,
        attrs_fn=_attr_uptime,
    ),
    APSensorDescription(
        key="ip",
        name="IP 地址",
        translation_key="ap_ip",
        icon="mdi:ip-network",
        value_fn=lambda a: a.ap_ip or a.ip or None,
    ),
    APSensorDescription(
        key="model",
        name="型号",
        translation_key="ap_model",
        icon="mdi:router-wireless",
        value_fn=lambda a: a.ap_model or None,
    ),
)

# ----------------------------------------------------------------------
# 终端传感器描述表（8 个）
# ----------------------------------------------------------------------
@dataclass(frozen=True, kw_only=True)
class ClientSensorDescription(SensorEntityDescription):
    """无线终端传感器描述。"""

    value_fn: Callable[[BeeconSta], float | int | str | None]
    attrs_fn: Callable[[BeeconSta], dict[str, object]] = mlo_attrs


CLIENT_SENSORS: tuple[ClientSensorDescription, ...] = (
    ClientSensorDescription(
        key="rssi",
        name="信号强度",
        translation_key="rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement="dBm",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:wifi-strength-2",
        value_fn=lambda s: s.rssi,
    ),
    ClientSensorDescription(
        key="band",
        name="频段",
        translation_key="client_band",
        icon="mdi:radio-tower",
        value_fn=lambda s: text_or_none(s.band),
    ),
    ClientSensorDescription(
        key="channel",
        name="信道",
        translation_key="client_channel",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:wifi",
        value_fn=lambda s: s.channel,
    ),
    ClientSensorDescription(
        key="phy",
        name="协议",
        translation_key="client_phy",
        icon="mdi:access-point-network",
        value_fn=lambda s: text_or_none(s.phy_mode),
    ),
    ClientSensorDescription(
        key="tx",
        name="发送速率",
        translation_key="client_tx",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:upload",
        value_fn=lambda s: s.tx_rate,
    ),
    ClientSensorDescription(
        key="rx",
        name="接收速率",
        translation_key="client_rx",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:download",
        value_fn=lambda s: s.rx_rate,
    ),
    ClientSensorDescription(
        key="ip",
        name="IP 地址",
        translation_key="client_ip",
        icon="mdi:ip-network",
        value_fn=lambda s: text_or_none(s.ip),
    ),
    ClientSensorDescription(
        key="mlo",
        name="MLO 多链路",
        translation_key="client_mlo",
        icon="mdi:link-variant",
        value_fn=lambda s: "是" if s.mlo else "否",
    ),
)


# ----------------------------------------------------------------------
# 平台入口
# ----------------------------------------------------------------------
async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """建立 AC 主机传感器 + 动态 AP 传感器 + 动态终端传感器。"""
    coordinator: BeeconMiniCoordinator = hass.data[DOMAIN][entry.entry_id]

    # AC 主机指标
    async_add_entities([BeeconACSensor(coordinator, d) for d in AC_SENSORS])

    # 动态 AP 传感器（1 个类由描述表驱动）
    known_aps: set[str] = set()

    @callback
    def _add_new_aps() -> None:
        new_entities: list[SensorEntity] = []
        for ap in coordinator.data.aps:
            if ap.mac in known_aps:
                continue
            known_aps.add(ap.mac)
            for desc in AP_SENSORS:
                new_entities.append(BeeconAPSensor(coordinator, ap.mac, desc))
        if new_entities:
            async_add_entities(new_entities)

    _add_new_aps()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_aps))

    # 动态无线终端传感器
    known_stas: set[str] = set()

    @callback
    def _add_new_clients() -> None:
        new_entities: list[SensorEntity] = []
        for sta in coordinator.data.stas:
            if sta.mac in known_stas:
                continue
            known_stas.add(sta.mac)
            for desc in CLIENT_SENSORS:
                new_entities.append(BeeconClientSensor(coordinator, sta.mac, desc))
        if new_entities:
            async_add_entities(new_entities)

    _add_new_clients()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_clients))


# ----------------------------------------------------------------------
# AC 主机传感器
# ----------------------------------------------------------------------
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


# ----------------------------------------------------------------------
# AP 传感器（1 个类由 AP_SENSORS 描述表驱动）
# ----------------------------------------------------------------------
class BeeconAPSensor(APEntityBase, SensorEntity):
    """AP 传感器。一台 AP 的 7 个传感器由 :data:`AP_SENSORS` 描述表驱动。"""

    entity_description: APSensorDescription

    def __init__(
        self,
        coordinator: BeeconMiniCoordinator,
        ap_mac: str,
        description: APSensorDescription,
    ) -> None:
        super().__init__(coordinator, ap_mac)
        self.entity_description = description
        self._attr_unique_id = f"{ap_mac}_{description.key}"
        self._attr_name = description.name
        self._attr_translation_key = description.translation_key
        self._attr_icon = description.icon
        self._attr_device_class = description.device_class
        self._attr_native_unit_of_measurement = description.native_unit_of_measurement
        self._attr_state_class = description.state_class

    @property
    def native_value(self) -> float | int | str | None:
        ap = self._ap
        return None if ap is None else self.entity_description.value_fn(ap)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        ap = self._ap
        return {} if ap is None else self.entity_description.attrs_fn(ap, self.coordinator)


# ----------------------------------------------------------------------
# 终端传感器
# ----------------------------------------------------------------------
class BeeconClientSensor(ClientEntityBase, SensorEntity):
    """无线终端的一个传感器（由 :data:`CLIENT_SENSORS` 描述表驱动）。

    一台终端的完整详情页 = 8 个本类实例（「传感器」区）+ 1 个「剔除终端」按钮
    （「控制」区）+ 设备信息卡片（型号 / 序列号 / **MAC** / 已连接到哪台 AP）。
    设备页由 HA 原生渲染：``via_device`` 指向所属 AP，因此 AP 设备页会出现
    「已连接的设备」卡片，点进去就是这里。
    """

    entity_description: ClientSensorDescription

    def __init__(
        self,
        coordinator: BeeconMiniCoordinator,
        sta_mac: str,
        description: ClientSensorDescription,
    ) -> None:
        super().__init__(coordinator, sta_mac)
        self.entity_description = description
        self._attr_unique_id = f"{sta_mac}_{description.key}"
        self._attr_name = description.name
        self._attr_translation_key = description.translation_key
        self._attr_device_class = description.device_class
        self._attr_native_unit_of_measurement = description.native_unit_of_measurement
        self._attr_state_class = description.state_class
        self._attr_icon = description.icon

    @property
    def native_value(self) -> float | int | str | None:
        sta = self._sta
        return None if sta is None else self.entity_description.value_fn(sta)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        sta = self._sta
        return {} if sta is None else self.entity_description.attrs_fn(sta)