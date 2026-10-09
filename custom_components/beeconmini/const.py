"""BeeconMini 无线 AC 集成 —— HA 侧常量。

⚠️ 与设备协议相关的东西（端点、act 码、枚举表）**不在本模块**，
   一律见 :mod:`api.protocol`。
"""
from __future__ import annotations

import json
from pathlib import Path

DOMAIN = "beeconmini"
NAME = "BeeconMini 无线 AC"

# 版本号单一来源：manifest.json（HACS 以此识别版本，不再重复维护）
with open(Path(__file__).parent / "manifest.json", "r", encoding="utf-8") as _f:
    VERSION = json.load(_f)["version"]

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
