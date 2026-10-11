"""BeeconMini 无线 AC 集成 —— HA 侧常量。

⚠️ 与设备协议相关的东西（端点、act 码、枚举表）**不在本模块**，
   一律见 :mod:`api.protocol`。

版本号与集成名称：均从 manifest.json 读取（本文件为唯一权威来源，
manifest.json 为运行时数据源）。CI 会校验（见 .github/workflows/release.yml）。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

DOMAIN = "beeconmini"

# ---- manifest.json 读取（VERSION 与 NAME 的唯一权威数据源）----
_MANIFEST_PATH = Path(__file__).parent / "manifest.json"
try:
    with open(_MANIFEST_PATH, "r", encoding="utf-8") as _f:
        _MANIFEST = json.load(_f)
    VERSION = _MANIFEST.get("version", "0.0.0")
    NAME = _MANIFEST.get("name", "BeeconMini AC")
except (OSError, json.JSONDecodeError) as e:
    _LOGGER.error("读取 manifest.json 失败: %s，使用默认值", e)
    VERSION = "0.0.0"
    NAME = "BeeconMini AC"

# ---- 配置项 ----
CONF_HOST = "host"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_VERIFY_SSL = "verify_ssl"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_USERNAME = "root"
DEFAULT_SCAN_INTERVAL = 30
DEFAULT_VERIFY_SSL = False

# ---- 设备信息 ----
MANUFACTURER = "BeeconMini"
MODEL_AC = "SEED AC Series"

# ---- 域级服务名 ----
SERVICE_KICK_CLIENT = "kick_client"        # 按 MAC 剔除终端
SERVICE_REBOOT_APS = "reboot_aps"          # 立即 / 定时重启全部纳管 AP
SERVICE_REBOOT_AP = "reboot_ap"            # 重启**单台** AP（cocmd 通道）
SERVICE_RUN_AP_COMMAND = "run_ap_command"  # 在**单台** AP 上执行 shell（⚠️ 实验性）
