"""传感器实体：AC 主机指标 + 每台 AP 的指标 + 每台无线终端的详情。

三层设备层级与实体归属：

* **AC 主机** —— 只放总数与主机自身指标（CPU / 内存 / 连接数 / 流量 / 终端数）；
* **每台 AP** —— 独立 device，`via_device` 指向 AC；信道 / 功率 / 无线终端数 /
  端口速率 / 运行时长 / IP / 型号；
* **每台无线终端** —— 独立 device，`via_device` 指向**所属 AP**，
  于是 AP 设备页会由 HA 原生渲染出「已连接的设备」卡片，
  点进任意一台终端即是它的详情页（9 个传感器 + 控制区剔除按钮）。
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

from .api import ACState, BeeconSta
from .base import (
    ACEntityBase,
    APEntityBase,
    ClientEntityBase,
    format_port_speed,
    format_power_level,
)
from .coordinator import BeeconMiniCoordinator
from .const import DOMAIN


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
            new_entities.append(BeeconAPChannelSensor(coordinator, ap.mac))
            new_entities.append(BeeconAPPowerSensor(coordinator, ap.mac))
            new_entities.append(BeeconAPClientSensor(coordinator, ap.mac))
            new_entities.append(BeeconAPPortSpeedSensor(coordinator, ap.mac))
            new_entities.append(BeeconAPUptimeSensor(coordinator, ap.mac))
            new_entities.append(BeeconAPIPSensor(coordinator, ap.mac))
            new_entities.append(BeeconAPModelSensor(coordinator, ap.mac))
        if new_entities:
            async_add_entities(new_entities)

    _add_new_aps()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_aps))

    # 动态无线终端传感器：每台终端 9 个（详见 CLIENT_SENSORS）
    known_stas: set[str] = set()

    @callback
    def _add_new_clients() -> None:
        new_entities: list[SensorEntity] = []
        for sta in coordinator.data.stas:
            if sta.mac in known_stas:
                continue
            known_stas.add(sta.mac)
            new_entities.extend(
                BeeconClientSensor(coordinator, sta.mac, desc) for desc in CLIENT_SENSORS
            )
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


class BeeconAPChannelSensor(APEntityBase, SensorEntity):
    """AP 信道信息（2.4G + 5G）。"""

    _attr_name = "信道"
    _attr_icon = "mdi:wifi"
    _attr_translation_key = "ap_channel"

    def __init__(self, coordinator: BeeconMiniCoordinator, ap_mac: str) -> None:
        super().__init__(coordinator, ap_mac)
        self._attr_unique_id = f"{ap_mac}_channel"

    @property
    def native_value(self) -> str | None:
        ap = self._ap
        if ap is None:
            return None
        c24 = str(ap.channel_24) if ap.channel_24 else "?"
        c5 = str(ap.channel_5) if ap.channel_5 else "?"
        return f"2.4G:{c24} 5G:{c5}"

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        ap = self._ap
        if ap is None:
            return {}
        return {
            "2.4G 信道": ap.channel_24,
            "5G 信道": ap.channel_5,
        }


class BeeconAPPowerSensor(APEntityBase, SensorEntity):
    """AP 功率档信息（2.4G + 5G）。"""

    _attr_name = "功率"
    _attr_icon = "mdi:signal-cellular-2"
    _attr_translation_key = "ap_power"

    def __init__(self, coordinator: BeeconMiniCoordinator, ap_mac: str) -> None:
        super().__init__(coordinator, ap_mac)
        self._attr_unique_id = f"{ap_mac}_power"

    @property
    def native_value(self) -> str | None:
        ap = self._ap
        if ap is None:
            return None
        p24 = format_power_level(ap.power_24_code) if ap.power_24_code is not None else "?"
        p5 = format_power_level(ap.power_5_code) if ap.power_5_code is not None else "?"
        return f"2.4G:{p24} 5G:{p5}"

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        ap = self._ap
        if ap is None:
            return {}
        return {
            "2.4G 功率": format_power_level(ap.power_24_code) if ap.power_24_code is not None else "未知",
            "5G 功率": format_power_level(ap.power_5_code) if ap.power_5_code is not None else "未知",
        }


def _sta_summary(sta) -> dict[str, object]:
    """一台无线终端的一行摘要（放进 AP「已连接设备」的属性里）。"""
    item: dict[str, object] = {
        "名称": sta.display_name,
        "MAC": sta.mac,
        "IP": sta.ip or "未知",
        "信号": f"{sta.rssi} dBm" if sta.rssi is not None else "未知",
        "频段": sta.band,
        "信道": sta.channel,
        "协议": sta.phy_mode,
        "Tx_Mbps": sta.tx_rate,
        "Rx_Mbps": sta.rx_rate,
    }
    if sta.mlo:
        item["MLO"] = True
        item["次链路信道"] = sta.channel_2
        item["次链路RSSI"] = f"{sta.rssi_2} dBm" if sta.rssi_2 is not None else "未知"
    return item


class BeeconAPClientSensor(APEntityBase, SensorEntity):
    """AP 无线终端数（= 该 AP 接入的无线终端台数）。

    **AP 下没有「有线终端」这一级** —— 有线终端直连 AC 的 LAN 口，
    口径见 :meth:`ACState.wired_users`。

    值口径（优先级）：act:34 按归属实测的台数 → ``a056 + a057``（act:31 自报）。
    **优先实测值**是为了让「计数」与「设备清单」严格一致 ——
    实测 AP 自报数会比实际归属多报（客厅自报 14 / 实际 12），
    用自报数会让 AC 侧总数也对不上。
    属性里的 ``终端列表`` 给出每台终端的名称 / MAC / IP / 信号 / 频段 / 信道 / 速率，
    便于模板与自动化直接取值；``AP 自报总数`` 保留厂商口径供排查。
    **逐台终端的实体在它自己的设备页上**
    （HA 会在本 AP 设备页渲染「已连接的设备」卡片列出它们）。

    ⚠️ v1.3.0 起本实体从「已连接设备」改名为「无线终端数」：
    「已连接的设备」现在是 HA 原生设备卡片的标题，两者同页会混淆。
    ``unique_id`` 仍是 ``{ap_mac}_clients``，改名不会重建实体。
    """

    _attr_name = "无线终端数"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:account-multiple"
    _attr_translation_key = "ap_clients"

    def __init__(self, coordinator: BeeconMiniCoordinator, ap_mac: str) -> None:
        super().__init__(coordinator, ap_mac)
        self._attr_unique_id = f"{ap_mac}_clients"

    @property
    def native_value(self) -> int | None:
        ap = self._ap
        return ap.sta_count if ap else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        ap = self._ap
        if ap is None:
            return {}
        stas = self.coordinator.data.stas_of_ap(ap.mac)
        return {
            "2.4G 终端": ap.users_24,
            "5G 终端": ap.users_5,
            # AP 自报数（a056+a057）只作对照：实测它会比实际归属多报几台
            "AP 自报总数": ap.ap_reported_count,
            "终端列表": [_sta_summary(s) for s in stas],
            "射频数": ap.radios,
        }


class BeeconAPPortSpeedSensor(APEntityBase, SensorEntity):
    """AP 端口协商速率。"""

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
        return ap.port_speed_text

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
    """AP 运行时长（秒）。"""

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
        if ap is None:
            return None
        # 取不到运行时长时给 None（HA 显示「未知」），而不是看着像真值的 0
        return ap.uptime_seconds or None

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


class BeeconAPIPSensor(APEntityBase, SensorEntity):
    """AP IP 地址。"""

    _attr_name = "IP 地址"
    _attr_icon = "mdi:ip-network"
    _attr_translation_key = "ap_ip"

    def __init__(self, coordinator: BeeconMiniCoordinator, ap_mac: str) -> None:
        super().__init__(coordinator, ap_mac)
        self._attr_unique_id = f"{ap_mac}_ip"

    @property
    def native_value(self) -> str | None:
        ap = self._ap
        if ap is None:
            return None
        return ap.ap_ip or ap.ip or None


class BeeconAPModelSensor(APEntityBase, SensorEntity):
    """AP 型号。"""

    _attr_name = "型号"
    _attr_icon = "mdi:router-wireless"
    _attr_translation_key = "ap_model"

    def __init__(self, coordinator: BeeconMiniCoordinator, ap_mac: str) -> None:
        super().__init__(coordinator, ap_mac)
        self._attr_unique_id = f"{ap_mac}_model"

    @property
    def native_value(self) -> str | None:
        ap = self._ap
        if ap is None:
            return None
        return ap.ap_model or None


# ----------------------------------------------------------------------
# 无线终端：一台终端 = 一个 device（via_device 指向所属 AP）
#   设备信息（HA 原生卡片）：型号 / 制造商 / 序列号 / **MAC**（来自 connections）
#                            + 「已连接到 AP · xx」的父设备链接
#   实体：8 个传感器（「传感器」区）+ 1 个剔除按钮（「控制」区，见 button.py）
#   —— MAC 不再做传感器，改由设备信息承载（HA 会把 connections 里的 mac
#      渲染成可点击的「MAC: xx:xx:…」，点进去是 DHCP 面板）
# ----------------------------------------------------------------------
def _text_or_none(value: object) -> str | None:
    """空串 / 占位符 → None（HA 显示「未知」而不是空行）。"""
    if value is None:
        return None
    text = str(value).strip()
    if text in ("", "-", "--", "无", "unknown", "Unknown", "N/A", "n/a"):
        return None
    return text


def _mlo_attrs(sta: BeeconSta) -> dict[str, object]:
    """MLO 次链路明细（单链路终端返回空）。

    主链路的信道 / 协议 / 速率 / 信号各自有独立实体，这里**只放次链路**，
    避免同页重复。
    """
    if not sta.mlo:
        return {}
    return {
        "次链路信道": sta.channel_2,
        "次链路协议": _text_or_none(sta.phy_mode_2),
        "次链路发送速率_Mbps": sta.tx_rate_2,
        "次链路接收速率_Mbps": sta.rx_rate_2,
        "次链路RSSI_dBm": sta.rssi_2,
    }


@dataclass(frozen=True, kw_only=True)
class ClientSensorDescription(SensorEntityDescription):
    """无线终端传感器描述。"""

    value_fn: Callable[[BeeconSta], float | int | str | None]
    attrs_fn: Callable[[BeeconSta], dict[str, object]] = _mlo_attrs


# 顺序即终端设备页上的排列顺序
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
        value_fn=lambda s: _text_or_none(s.band),
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
        value_fn=lambda s: _text_or_none(s.phy_mode),
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
        value_fn=lambda s: _text_or_none(s.ip),
    ),
    ClientSensorDescription(
        key="mlo",
        name="MLO 多链路",
        translation_key="client_mlo",
        icon="mdi:link-variant",
        value_fn=lambda s: "是" if s.mlo else "否",
    ),
)


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
        # unique_id 保持 ``{mac}_rssi`` 这类历史形态，改名不会重建已有实体
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
