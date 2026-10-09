"""BeeconMini AC 域级服务：kick_client / reboot_aps / reboot_ap / run_ap_command。"""
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
from .const import (
    DOMAIN,
    SERVICE_KICK_CLIENT,
    SERVICE_REBOOT_AP,
    SERVICE_REBOOT_APS,
    SERVICE_RUN_AP_COMMAND,
)
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

# 单台 AP：MAC 或 AP 名称，二选一
_MAC_OR_NAME = {
    vol.Optional("mac"): cv.string,
    vol.Optional("name"): cv.string,
}
SCHEMA_REBOOT_AP = vol.Schema(_MAC_OR_NAME)
SCHEMA_RUN_AP_COMMAND = vol.Schema(
    {
        **_MAC_OR_NAME,
        vol.Required("command"): cv.string,
    }
)


def _get_coordinator(hass: HomeAssistant) -> BeeconMiniCoordinator:
    """取配置项的协调器；同一域名下只支持单实例。"""
    coordinators = hass.data.get(DOMAIN, {})
    if not coordinators:
        raise HomeAssistantError("BeeconMini AC 尚未配置")
    return next(iter(coordinators.values()))


def _resolve_ap_mac(coordinator: BeeconMiniCoordinator, data: dict) -> str:
    """把服务参数里的 ``mac`` / ``name`` 解析成一个**在线或已知**的 AP MAC。

    支持三种写法：
    * ``mac:`` —— 直接给 MAC（``aa:bb:cc:dd:ee:ff`` 或 ``aa-bb-...``）；
    * ``name:`` —— 给 AP 名称（设备页上显示的名字，如「悦房」）；
    * 两者都给 —— ``mac`` 优先。
    """
    raw_mac = (data.get("mac") or "").strip()
    raw_name = (data.get("name") or "").strip()
    if raw_mac:
        mac = normalize_mac(raw_mac)
        if not is_mac(mac):
            raise HomeAssistantError(
                f"MAC 地址格式不合法：{raw_mac!r}（示例 aa:bb:cc:dd:ee:ff）"
            )
        return mac
    if not raw_name:
        raise HomeAssistantError("必须提供 mac 或 name 之一（目标 AP）")
    hits = [
        ap
        for ap in coordinator.data.aps
        if raw_name in (ap.name or "", ap.display_name or "")
    ]
    if not hits:
        known = "、".join(ap.display_name for ap in coordinator.data.aps) or "（暂无）"
        raise HomeAssistantError(f"找不到名为 {raw_name!r} 的 AP；已知：{known}")
    if len(hits) > 1:
        raise HomeAssistantError(
            f"名称 {raw_name!r} 匹配到多台 AP，请改用 mac 指定："
            + "、".join(f"{ap.display_name}({ap.mac})" for ap in hits)
        )
    return hits[0].mac


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

    * 不传 hour/minute → **立即重启**（走 ``api.protocol.CMD_REBOOT_APS``
      这条固件真实通道，等价于按复位键；全部 AP 重启、AC 自身不重启，
      约 40–110 秒后自动回上线）
    * 传 hour/minute → 写入**定时重启**计划（``csdp.sys.rben…``）
    """
    coordinator = _get_coordinator(call.hass)
    data = call.data
    try:
        if data.get("hour") is None or data.get("minute") is None:
            ok = await coordinator.client.async_reboot_all_aps()
            desc = "立即（全部 AP，约 1 分钟后掉线并自行回上线）"
        else:
            day = int(data.get("day") or 8)
            enabled = data.get("enabled", True)
            ok = await coordinator.client.async_set_reboot_schedule(
                enabled, day, data["hour"], data["minute"]
            )
            desc = f"day={day} {data['hour']:02d}:{data['minute']:02d}"
            # 写是能写进去的（回读会确认），但固件**不会执行**：
            # csdp_reboot.c 最终要跑的重启脚本不在插件包里（详见 api/protocol.py）。
            _LOGGER.warning(
                "定时重启计划已写入（%s），但固件不会执行它 —— "
                "插件包缺少重启脚本，届时不会有任何重启。"
                "如需重启请不填 hour/minute（走真实通道）。",
                desc,
            )
    except (BeeconMiniAuthError, BeeconMiniApiError, BeeconMiniConnectionError) as err:
        raise HomeAssistantError(f"安排 AP 重启失败：{err}") from err

    if ok:
        _LOGGER.info("AP 重启已下发：%s", desc)
    else:
        _LOGGER.warning("AP 重启未确认成功：%s", desc)
    await coordinator.async_request_refresh()


async def _handle_reboot_ap(call: ServiceCall) -> None:
    """重启**单台** AP（仅目标 AP 重启，其余 AP 不受影响）。

    走厂商固件的 ``cocmd`` 通道：``bxplug -m "urtm:sm:comsg:sm:cocmd:cmd:<mac>reboot"``。
    定向由 MAC 决定，AC 自身不重启。
    """
    coordinator = _get_coordinator(call.hass)
    mac = _resolve_ap_mac(coordinator, call.data)
    ap = coordinator.data.ap_by_mac(mac)
    label = ap.display_name if ap else mac
    try:
        await coordinator.client.async_reboot_ap(mac)
    except (BeeconMiniAuthError, BeeconMiniApiError, BeeconMiniConnectionError) as err:
        raise HomeAssistantError(f"重启 AP「{label}」失败：{err}") from err
    _LOGGER.info(
        "已下发单台 AP 重启：%s（%s）—— 其余 AP 不受影响，约 1–2 分钟后自行回上线",
        label,
        mac,
    )
    await coordinator.async_request_refresh()


async def _handle_run_ap_command(call: ServiceCall) -> None:
    """在**指定的那一台 AP** 上执行一段 shell，并把该 AP 的 stdout 写进日志。

    ⚠️ 这是固件原生的 AC→AP shell 通道，命令以 **root** 在 AP 上 ``eval``。
    属于「排障用法」：适合 ``logread`` / ``iwinfo`` / ``cat /proc/...`` 这类
    只读查看；**不要**在自动化里跑会改配置或重启服务的命令。
    命令导致 AP 掉线时（如 ``reboot``）拿不到回显，属正常现象。
    """
    coordinator = _get_coordinator(call.hass)
    mac = _resolve_ap_mac(coordinator, call.data)
    command = call.data["command"]
    ap = coordinator.data.ap_by_mac(mac)
    label = ap.display_name if ap else mac
    try:
        out = await coordinator.client.async_run_ap_command(mac, command)
    except (BeeconMiniAuthError, BeeconMiniApiError, BeeconMiniConnectionError) as err:
        raise HomeAssistantError(f"在 AP「{label}」执行命令失败：{err}") from err
    _LOGGER.warning(
        "AP「%s」(%s) 执行 `%s` 的输出：\n%s",
        label,
        mac,
        command,
        (out or "").strip() or "（无回显：命令仍在执行，或该命令使 AP 掉了线）",
    )
    # 顺手把结果挂到事件总线上，方便自动化/脚本直接取用（日志里不好抓）
    coordinator.hass.bus.async_fire(
        f"{DOMAIN}_ap_command_result",
        {
            "ap_mac": mac,
            "ap_name": label,
            "command": command,
            "output": out,
        },
    )


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """注册域级服务（幂等）。"""
    services = (
        (SERVICE_KICK_CLIENT, _handle_kick_client, SCHEMA_KICK_CLIENT),
        (SERVICE_REBOOT_APS, _handle_reboot_aps, SCHEMA_REBOOT_APS),
        (SERVICE_REBOOT_AP, _handle_reboot_ap, SCHEMA_REBOOT_AP),
        (SERVICE_RUN_AP_COMMAND, _handle_run_ap_command, SCHEMA_RUN_AP_COMMAND),
    )
    for name, handler, schema in services:
        if not hass.services.has_service(DOMAIN, name):
            hass.services.async_register(DOMAIN, name, handler, schema=schema)
