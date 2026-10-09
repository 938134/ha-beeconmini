"""BeeconMini AC 诊断信息导出。

通过 HA 原生诊断机制导出配置条目信息，方便排障。
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import BeeconMiniCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """导出配置条目诊断信息。"""
    coordinator: BeeconMiniCoordinator = hass.data[DOMAIN][entry.entry_id]
    state = coordinator.data

    # 设备信息
    dev = state.device
    device_info = {
        "model": dev.model,
        "version": dev.version,
        "sn": dev.sn,
        "mac": dev.mac,
        "cpu_temp": dev.cpu_temp,
        "cpu_usage": dev.cpu_usage,
        "mem_usage": dev.mem_usage,
        "conn_num": dev.conn_num,
    }

    # AP 清单
    ap_list = []
    for ap in state.aps:
        ap_list.append({
            "mac": ap.mac,
            "name": ap.name,
            "online": ap.online,
            "radios": ap.radios,
            "channel_24": ap.channel_24,
            "channel_5": ap.channel_5,
            "power_24_code": ap.power_24_code,
            "power_5_code": ap.power_5_code,
            "ap_model": ap.ap_model,
            "ap_version": ap.ap_version,
            "ap_ip": ap.ap_ip,
            "ap_sn": ap.ap_sn,
            "port_speed_code": ap.port_speed_code,
            "port_cap_code": ap.port_cap_code,
            "port_plug": ap.port_plug,
            "uptime_seconds": ap.uptime_seconds,
            "users_24": ap.users_24,
            "users_5": ap.users_5,
            "led_state": ap.led_state,
            "sta_count": ap.sta_count,
            "sta_count_measured": ap.sta_count_measured,
            "ap_reported_count": ap.ap_reported_count,
        })

    # 终端清单
    sta_list = []
    for sta in state.stas:
        sta_list.append({
            "mac": sta.mac,
            "ip": sta.ip,
            "hostname": sta.hostname,
            "ap_mac": sta.ap_mac,
            "ap_name": sta.ap_name,
            "ap_model": sta.ap_model,
            "band": sta.band,
            "channel": sta.channel,
            "rssi": sta.rssi,
            "phy_mode": sta.phy_mode,
            "tx_rate": sta.tx_rate,
            "rx_rate": sta.rx_rate,
            "mlo": sta.mlo,
            "channel_2": sta.channel_2,
            "rssi_2": sta.rssi_2,
            "phy_mode_2": sta.phy_mode_2,
            "tx_rate_2": sta.tx_rate_2,
            "rx_rate_2": sta.rx_rate_2,
        })

    # 有线终端
    wired = []
    for user in state.wired_users():
        wired.append({
            "mac": user.mac,
            "ip": user.ip,
            "hostname": user.hostname,
            "gateway": user.gateway,
        })

    # 漫游策略
    rpolicy = None
    if state.rpolicy:
        rp = state.rpolicy
        rpolicy = {
            "r24_roaming_trigger_dbm": rp.r24_roaming_trigger_dbm,
            "r5_roaming_trigger_dbm": rp.r5_roaming_trigger_dbm,
            "r24_eviction_threshold_dbm": rp.r24_eviction_threshold_dbm,
            "r5_eviction_threshold_dbm": rp.r5_eviction_threshold_dbm,
            "load_balance_rssi_dbm": rp.load_balance_rssi_dbm,
            "r24_max_clients_per_radio": rp.r24_max_clients_per_radio,
            "r5_max_clients_per_radio": rp.r5_max_clients_per_radio,
            "r24_eviction_enabled": rp.r24_eviction_enabled,
            "r5_eviction_enabled": rp.r5_eviction_enabled,
        }

    # 传输状态
    transport = coordinator.client.transport
    transport_info = {
        "host": coordinator.client.base_url,
        "ubus_token": transport._ubus_token is not None,
        "sysauth": transport._sysauth is not None,
    }

    return {
        "config": {k: v for k, v in entry.data.items() if k != "password"},
        "options": dict(entry.options),
        "version": coordinator.client.transport.__class__.__module__.rsplit(".", 1)[0].split(".")[-1],
        "scan_interval": coordinator.update_interval.total_seconds() if coordinator.update_interval else None,
        "device": device_info,
        "state": {
            "wan_rx_bytes": state.wan_rx_bytes,
            "wan_tx_bytes": state.wan_tx_bytes,
            "lan_ip": state.lan_ip,
            "wireless_client_count": state.wireless_client_count,
            "wired_client_count": state.wired_client_count,
            "ap_online_count": state.ap_online_count,
            "errors": state.errors,
        },
        "aps": ap_list,
        "stas": sta_list,
        "wired_users": wired,
        "rpolicy": rpolicy,
        "transport": transport_info,
    }