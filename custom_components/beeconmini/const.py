"""BeeconMini 无线 AC 集成 —— 常量定义。"""

import json
from pathlib import Path

DOMAIN = "beeconmini"
NAME = "BeeconMini 无线 AC"

# 版本号单一来源：manifest.json（HACS 以此识别版本，不再重复维护）
with open(Path(__file__).parent / "manifest.json", "r", encoding="utf-8") as _f:
    VERSION = json.load(_f)["version"]

# ---- LuCI 认证与 RPC 端点 ----
LUCI_LOGIN_PATH = "/cgi-bin/luci/"
LUCI_WEB_ACTION = "/cgi-bin/luci/admin/beeconmini2/web_action"
UBUS_PATH = "/ubus"

# ---- CSDP 直通端点（POST 紧凑 JSON） ----
#   /api         → /tmp/bxplug.sock   漫游/踢人/终端明细等 act
#   /rtlgsw_api  → /tmp/rtlgsw.sock   PoE（已移除）
CSDP_API_PATH = "/cgi-bin/luci/admin/beeconmini2/api"

# ---- ubus 匿名会话 token（用于 session.login） ----
UBUS_NULL_TOKEN = "00000000000000000000000000000000"

# ---- CSDP 本地命令入口（路由器内置 lua 脚本） ----
LOCAL_CMD_SCRIPT = "/usr/share/rtmgr/local_cmd.lua"

# ---- 写操作：AP 定时重启（csdp.sys.rben/rbday/rbhour/rbminute） ----
CSDP_UCI = "csdp"
RB_DEFAULT_DAY = 8  # 1-7=周一至周日，8=每天
RB_DEFAULT_HOUR = 0
RB_DEFAULT_MINUTE = 0

# ---- CSDP act 码（实测确认，均为紧凑 JSON POST） ----
ACT_KICK = 249        # bxplug：剔除终端（厂商原生）
ACT_STA_GET = 34      # bxplug：无线终端明细（含所属 AP / RSSI / 信道）
ACT_AP_DETAILS = 31    # bxplug：AP 管理/状态页（型号/版本/端口速率）
ACT_ROAMING_READ = 248   # bxplug：漫游策略读（只读，不写回）

# ---- 本地 lua 命令 action ----
ACTION_STATUS = "status"
ACTION_USERS = "getusers"
ACTION_PRODUCT = "getproductinfo"
ACTION_WAN_STATS = "wan_usb_stats"

# ---- 域级服务名 ----
SERVICE_KICK_CLIENT = "kick_client"    # 按 MAC 剔除终端
SERVICE_REBOOT_APS = "reboot_aps"      # 立即 / 定时重启全部纳管 AP

# ---- AC 运行时数据快照文件 ----
JSON_SNAPSHOT_FILES = {
    "aps": "/tmp/json/apinfos",
    "rpolicys": "/tmp/json/rpolicys",
}

# ---- 功率档映射（act:31 r03/r13） ----
POWER_LEVELS = {
    0: "极低",
    1: "低",
    2: "中",
    3: "高",
}

# ---- 端口速率映射（act:31 s07/a041） ----
PORT_SPEED_MAP = {
    0: "10M",
    1: "100M",
    2: "1000M",
    3: "2500M",
}

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
