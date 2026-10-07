"""按钮平台：AP 重启 + 终端剔除。"""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import BeeconMiniAuthError, BeeconMiniApiError
from .base import APEntityBase, ClientEntityBase
from .coordinator import BeeconMiniCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """建立 AP 重启按钮 + 终端剔除按钮。"""
    coordinator: BeeconMiniCoordinator = hass.data[DOMAIN][entry.entry_id]

    # 动态 AP 重启按钮
    known_aps: set[str] = set()

    @callback
    def _add_new_aps() -> None:
        new_entities: list[ButtonEntity] = []
        for ap in coordinator.data.aps:
            if ap.mac in known_aps:
                continue
            known_aps.add(ap.mac)
            new_entities.append(BeeconAPRebootButton(coordinator, ap.mac))
        if new_entities:
            async_add_entities(new_entities)

    _add_new_aps()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_aps))

    # 动态终端剔除按钮
    known_stas: set[str] = set()

    @callback
    def _add_new_clients() -> None:
        new_entities: list[ButtonEntity] = []
        for sta in coordinator.data.stas:
            if sta.mac in known_stas:
                continue
            known_stas.add(sta.mac)
            new_entities.append(BeeconClientKickButton(coordinator, sta.mac))
        if new_entities:
            async_add_entities(new_entities)

    _add_new_clients()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_clients))


class BeeconAPRebootButton(APEntityBase, ButtonEntity):
    """单台 AP 重启按钮。"""

    _attr_name = "重启 AP"
    _attr_icon = "mdi:restart"
    _attr_translation_key = "reboot_ap"

    def __init__(self, coordinator: BeeconMiniCoordinator, ap_mac: str) -> None:
        super().__init__(coordinator, ap_mac)
        self._attr_unique_id = f"{ap_mac}_reboot"

    async def async_press(self) -> None:
        """重启当前 AP。

        固件不支持单 AP 重启时，降级为重启全部 AP（1 分钟后）。
        """
        client = self.coordinator.client
        try:
            ok = await client.async_reboot_all_aps()
        except (BeeconMiniAuthError, BeeconMiniApiError) as err:
            raise HomeAssistantError(f"重启 AP 失败：{err}" ) from err
        if not ok:
            raise HomeAssistantError("重启 AP 未确认成功")
        _LOGGER.info("AP 重启已安排：1 分钟后")
        await self.coordinator.async_request_refresh()


class BeeconClientKickButton(ClientEntityBase, ButtonEntity):
    """终端剔除按钮（act:249）。

    终端的 RSSI / 信道 / Tx-Rx 速率等挂在 extra_state_attributes 上，
    不新增独立 sensor，保持实体数量精简。
    """

    _attr_name = "剔除终端"
    _attr_icon = "mdi:wifi-off"
    _attr_translation_key = "kick_client"

    def __init__(self, coordinator: BeeconMiniCoordinator, sta_mac: str) -> None:
        super().__init__(coordinator, sta_mac)
        self._attr_unique_id = f"{sta_mac}_kick"

    async def async_press(self) -> None:
        """剔除当前终端（厂商原生 act:249，一次性断连）。"""
        client = self.coordinator.client
        mac = self._sta_mac
        try:
            ok = await client.async_deauth_client(mac)
        except (BeeconMiniAuthError, BeeconMiniApiError) as err:
            raise HomeAssistantError(f"剔除终端失败：{err}") from err
        if ok:
            _LOGGER.info("已剔除终端：%s", mac)
        else:
            _LOGGER.warning("剔除终端 %s 未确认成功", mac)
        await self.coordinator.async_request_refresh()

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
            "AP型号": sta.ap_model,
            "频段": sta.band,
            "信道": sta.channel,
            "RSSI_dBm": sta.rssi,
            "协议": sta.phy_mode,
            "Tx_Mbps": sta.tx_rate,
            "Rx_Mbps": sta.rx_rate,
            "MLO": sta.mlo,
        }
