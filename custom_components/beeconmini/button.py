"""按钮平台：单台 AP 重启 / 全部 AP 重启 / 终端剔除。"""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import (
    BeeconMiniApiError,
    BeeconMiniAuthError,
    BeeconMiniConnectionError,
)
from .base import ACEntityBase, APEntityBase, ClientEntityBase
from .coordinator import BeeconMiniCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """建立「重启单台 AP」按钮（每台一个）+ AC 页的「重启全部 AP」+ 终端剔除按钮。"""
    coordinator: BeeconMiniCoordinator = hass.data[DOMAIN][entry.entry_id]

    # AC 设备页：整机重启全部 AP（保留 v1.3.x 的能力，但只放一个入口）
    async_add_entities([BeeconAllAPsRebootButton(coordinator)])

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
    """**重启这一台** AP 的按钮（落在该 AP 设备页的「控制」区）。

    走厂商固件的 ``cocmd`` 通道（AC→AP 的 shell 下发）：

        bxplug -m "urtm:sm:comsg:sm:cocmd:cmd:<18字符MAC>reboot"

    定向由 MAC 决定 —— 2026-10-09 实机验证：只有按钮对应的那台 AP
    运行时长归零，同网另外两台 AP 运行时长连续增长，**AC 自身不重启**。

    ⚠️ 与 AC 设备页那个「重启全部 AP」不是一回事：
    那个走 ``csdp:reboot``（等价按复位键），会把**所有**纳管 AP 一起重启。
    """

    _attr_name = "重启此 AP"
    _attr_icon = "mdi:restart"
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_translation_key = "reboot_this_ap"

    def __init__(self, coordinator: BeeconMiniCoordinator, ap_mac: str) -> None:
        super().__init__(coordinator, ap_mac)
        self._attr_unique_id = f"{ap_mac}_reboot"

    async def async_press(self) -> None:
        """只重启本按钮所属的那台 AP。"""
        client = self.coordinator.client
        mac = self._ap_mac
        ap = self._ap
        label = ap.display_name if ap else mac
        try:
            ok = await client.async_reboot_ap(mac)
        except (BeeconMiniAuthError, BeeconMiniApiError, BeeconMiniConnectionError) as err:
            raise HomeAssistantError(f"重启 AP「{label}」失败：{err}") from err
        if not ok:
            raise HomeAssistantError(f"重启 AP「{label}」未确认下发")
        _LOGGER.info(
            "已下发单台 AP 重启：%s（%s）—— 其余 AP 不受影响，约 1–2 分钟后自行回上线",
            label,
            mac,
        )
        await self.coordinator.async_request_refresh()


class BeeconAllAPsRebootButton(ACEntityBase, ButtonEntity):
    """**重启全部纳管 AP** 的按钮（落在 AC 设备页的「控制」区）。

    走真实的 ``bxplug -m "csdp:reboot"`` 通道（等价于按复位键）：
    全部 AP 会一起重启，**AC 自身不重启**，约 40–110 秒后自行回上线。

    只保留这一个入口 —— 单台重启请用各台 AP 设备页上的「重启此 AP」，
    那个走定向的 ``cocmd`` 通道，不会波及别的 AP。
    """

    _attr_name = "重启全部 AP"
    _attr_icon = "mdi:restart-alert"
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_translation_key = "reboot_all_aps"

    def __init__(self, coordinator: BeeconMiniCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_reboot_all_aps"

    async def async_press(self) -> None:
        """重启全部纳管 AP（固件真实通道，AC 自身不重启）。"""
        client = self.coordinator.client
        try:
            ok = await client.async_reboot_all_aps()
        except (BeeconMiniAuthError, BeeconMiniApiError, BeeconMiniConnectionError) as err:
            raise HomeAssistantError(f"重启全部 AP 失败：{err}") from err
        if not ok:
            raise HomeAssistantError("重启全部 AP 未确认成功")
        _LOGGER.info("已下发 AP 重启：全部纳管 AP 将在数十秒内重启并自动回上线")
        await self.coordinator.async_request_refresh()


class BeeconClientKickButton(ClientEntityBase, ButtonEntity):
    """终端剔除按钮（act:249），落在终端设备页的「控制」区。

    一台无线终端一个按钮 —— 但按钮挂在该终端**自己的设备页**上，
    而终端又挂在所属 AP 之下（HA 会在 AP 设备页渲染「已连接的设备」
    卡片把它们列出来），所以不会像以前那样散在总览里。

    ⚠️ **不要**设 ``entity_category``：HA 设备页按 ``entity_category`` 分区，
    没有分类的 button 才会落在「控制」区（与 AP 页的「重启 AP」同区）；
    设成 CONFIG 会被归到「配置」区，设成 DIAGNOSTIC 会进「诊断」区。

    终端的链路详情（信号/信道/协议/速率/…）由 sensor.py 的 8 个传感器承载，
    MAC 由设备信息（DeviceInfo.connections）承载，这里只在属性里留一眼就能
    看懂的归属信息。
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
        except (BeeconMiniAuthError, BeeconMiniApiError, BeeconMiniConnectionError) as err:
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
            "MAC": sta.mac,
            "所属AP": sta.ap_name or sta.ap_mac,
            "AP型号": sta.ap_model,
            "频段": sta.band,
            "信号": f"{sta.rssi} dBm" if sta.rssi is not None else "未知",
            "MLO": sta.mlo,
        }
