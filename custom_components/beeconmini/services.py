"""BeeconMini AC 域级服务：kick_client / reboot_aps。"""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .api import (
    BeeconMiniApiError,
    BeeconMiniAuthError,
    BeeconMiniConnectionError,
    is_mac,
    normalize_mac,
)
from .const import DOMAIN, SERVICE_KICK_CLIENT, SERVICE_REBOOT_APS
from .coordinator import BeeconMiniCoordinator

_LOGGER = logging.getLogger(__name__)

SCHEMA_KICK_CLIENT = vol.Schema(
    {
        vol.Required("mac"): cv.string,
    }
)

SCHEMA_REBOOT_APS = vol.Schema(
    {
        vol.Optional("hour"): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
        vol.Optional("minute"): vol.All(vol.Coerce(int), vol.Range(min=0, max=59)),
        vol.Optional("day", default=8): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=8)
        ),
        vol.Optional("enabled", default=True): bool,
    }
)


def _get_coordinator(hass: HomeAssistant) -> BeeconMiniCoordinator:
    """取配置项的协调器；同一域名下只支持单实例。"""
    coordinators = hass.data.get(DOMAIN, {})
    if not coordinators:
        raise HomeAssistantError("BeeconMini AC 尚未配置")
    return next(iter(coordinators.values()))


async def _handle_kick_client(call: ServiceCall) -> None:
    """剔除指定 MAC 的无线终端（厂商原生 act:249，一次性断连）。"""
    coordinator = _get_coordinator(call.hass)
    raw = call.data["mac"]
    mac = normalize_mac(raw)
    if not is_mac(mac):
        raise HomeAssistantError(
            f"MAC 地址格式不合法：{raw!r}（示例 aa:bb:cc:dd:ee:ff）"
        )
    try:
        ok = await coordinator.client.async_deauth_client(mac)
    except (BeeconMiniAuthError, BeeconMiniApiError, BeeconMiniConnectionError) as err:
        raise HomeAssistantError(f"剔除终端失败：{err}") from err
    if ok:
        _LOGGER.info("已剔除终端：%s", mac)
    else:
        _LOGGER.warning("剔除终端 %s 未确认成功", mac)
    await coordinator.async_request_refresh()


async def _handle_reboot_aps(call: ServiceCall) -> None:
    """立即或定时重启全部纳管 AP。

    不传 hour/minute → 立即重启（取路由器当前时间 +1 分钟作为一次性重启点）
    传 hour/minute → 写入定时重启计划
    """
    coordinator = _get_coordinator(call.hass)
    data = call.data
    try:
        if data.get("hour") is None or data.get("minute") is None:
            ok = await coordinator.client.async_reboot_all_aps()
            desc = "1 分钟后（立即重启）"
        else:
            day = int(data.get("day") or 8)
            enabled = data.get("enabled", True)
            ok = await coordinator.client.async_set_reboot_schedule(
                enabled, day, data["hour"], data["minute"]
            )
            desc = f"day={day} {data['hour']:02d}:{data['minute']:02d}"
    except (BeeconMiniAuthError, BeeconMiniApiError, BeeconMiniConnectionError) as err:
        raise HomeAssistantError(f"安排 AP 重启失败：{err}") from err

    if ok:
        _LOGGER.info("AP 重启已安排：%s", desc)
    else:
        _LOGGER.warning("AP 重启安排未确认成功：%s", desc)
    await coordinator.async_request_refresh()


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """注册域级服务（幂等）。"""
    services = (
        (SERVICE_KICK_CLIENT, _handle_kick_client, SCHEMA_KICK_CLIENT),
        (SERVICE_REBOOT_APS, _handle_reboot_aps, SCHEMA_REBOOT_APS),
    )
    for name, handler, schema in services:
        if not hass.services.has_service(DOMAIN, name):
            hass.services.async_register(DOMAIN, name, handler, schema=schema)
