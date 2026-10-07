"""BeeconMini 无线 AC 集成入口。

平台：sensor / binary_sensor / button
服务：kick_client / reboot_aps

三层设备层级：
  AC 主设备 → 每台 AP（via=AC） → 每台终端（via=AP）

- AC：CPU/内存/连接数、WAN 收发流量、终端数、AP 状态、漫游剔除策略
- AP：在线状态、信道、功率、用户数、端口速率、运行时长、IP、型号、重启按钮
- 终端：RSSI 信号强度、剔除按钮
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .api import BeeconMiniClient
from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USERNAME,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    NAME,
    VERSION,
)
from .coordinator import BeeconMiniCoordinator
from .services import async_register_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """建立配置项。"""
    host = entry.data[CONF_HOST]
    opts = {**entry.data, **entry.options}

    client = BeeconMiniClient(
        host=host,
        username=opts.get(CONF_USERNAME, DEFAULT_USERNAME),
        password=opts[CONF_PASSWORD],
        verify_ssl=opts.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
    )

    coordinator = BeeconMiniCoordinator(
        hass,
        client,
        scan_interval=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_register_services(hass)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """卸载配置项。"""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: BeeconMiniCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """选项变更后重载。"""
    await hass.config_entries.async_reload(entry.entry_id)
