"""传感器实体：AC 主机指标 + 每台 AP 的终端数与终端清单。"""
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
    EntityCategory,
    UnitOfInformation,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .base import ACEntityBase, APEntityBase
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
        key="wifi_clients",
        translation_key="wifi_clients",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:wifi",
        value_fn=lambda s: s.wireless_client_count,
    ),
    ACSensorDescription(
        key="wired_clients",
        translation_key="wired_clients",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:ethernet",
        value_fn=lambda s: s.wired_client_count,
    ),
    ACSensorDescription(
        key="wan_rx",
        translation_key="wan_rx",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        value_fn=lambda s: s.wan_rx_bytes,
    ),
    ACSensorDescription(
        key="wan_tx",
        translation_key="wan_tx",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        value_fn=lambda s: s.wan_tx_bytes,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """建立 AC 主机传感器 + 动态发现的 AP 传感器。"""
    coordinator: BeeconMiniCoordinator = hass.data[DOMAIN][entry.entry_id]

    # 静态：AC 主机指标
    async_add_entities(
        [BeeconACSensor(coordinator, desc) for desc in AC_SENSORS]
    )

    # 动态：每台 AP 的接入终端数 + 终端清单
    known_aps: set[str] = set()

    @callback
    def _add_new_aps() -> None:
        new_entities: list[SensorEntity] = []
        for ap in coordinator.data.aps:
            if ap.mac in known_aps:
                continue
            known_aps.add(ap.mac)
            new_entities.append(BeeconAPClientSensor(coordinator, ap.mac))
            new_entities.append(BeeconAPClientListSensor(coordinator, ap.mac))
        if new_entities:
            async_add_entities(new_entities)

    _add_new_aps()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_aps))


class BeeconACSensor(ACEntityBase, SensorEntity):
    """AC 主机传感器。"""

    entity_description: ACSensorDescription

    def __init__(
        self, coordinator: BeeconMiniCoordinator, description: ACSensorDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{description.key}"

    @property
    def native_value(self) -> float | int | str | None:
        return self.entity_description.value_fn(self.coordinator.data)


class BeeconAPClientSensor(APEntityBase, SensorEntity):
    """单台 AP 的接入终端数（属性含 MAC/IP/射频数/负载）。"""

    _attr_name = "接入终端数"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:account-multiple"

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
            "ip": ap.ip,
            "有线终端": ap.wired_clients,
            "无线终端": ap.wireless_clients,
            "已纳管": ap.configured,
            "射频数": ap.radios,
            "负载指标": ap.load,
        }


class BeeconAPClientListSensor(APEntityBase, SensorEntity):
    """单台 AP 的无线终端清单（value = 无线终端数，属性含每台终端链路详情）。

    数据来源是 act:34——唯一能拿到「终端 → 所属 AP」归属关系的接口。
    """

    _attr_name = "终端清单"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:account-network"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: BeeconMiniCoordinator, ap_mac: str) -> None:
        super().__init__(coordinator, ap_mac)
        self._attr_unique_id = f"{ap_mac}_client_list"

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data.stas_of_ap(self._ap_mac))

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        stas = self.coordinator.data.stas_of_ap(self._ap_mac)
        return {
            "无线终端": [
                {
                    "名称": s.display_name,
                    "ip": s.ip,
                    "mac": s.mac,
                    "频段": s.band,
                    "信道": s.channel,
                    "RSSI_dBm": s.rssi,
                    "协议": s.phy_mode,
                    "Tx_Mbps": s.tx_rate,
                    "Rx_Mbps": s.rx_rate,
                    "MLO": s.mlo,
                }
                for s in stas
            ],
            "无线终端数": len(stas),
        }
