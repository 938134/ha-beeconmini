"""BeeconMini 无线 AC —— 协议事实与取数策略（L0）。

本模块只记录「协议是什么」，不含任何请求逻辑，也不依赖其它模块：

* 端点、act 码、action 字、UCI 键 —— 全部**实测**得出；
* 每个数据集的**来源**在文件末尾集中声明。

⚠️ 两个必须记住的坑
  1. CSDP 直通端点必须发**紧凑 JSON**（``{"act":31}`` 而非 ``{"act": 31}``），
     固件侧是手写解析器，见 :meth:`transport.AiohttpTransport.csdp`。
  2. AP 的 ``r02/r12/r03/r13`` 分别是 2.4G/5G 信道与 2.4G/5G 功率档 ——
     不是负载，也不是终端数。

📌 v1.3.3 起**不再使用**路由器上的 ``/tmp/json/*`` 快照文件（原 ``apinfos`` /
``rpolicys``）。实测那批文件是**静态遗留**：只在配置变更时才写一次
（本机 apinfos 停在 12 天前、baseinfo/devinfo 停在 8 个月前），
且 apinfos 单条只有 11 个字段、信道值 6 个里 4 个与实际不符。
把它们当「实时读数的兜底」只会把过期配置值显示成实时值 —— 弊大于利。
"""

from __future__ import annotations

# ----------------------------------------------------------------------
# 端点
# ----------------------------------------------------------------------
PATH_LUCI_LOGIN = "/cgi-bin/luci/"
PATH_WEB_ACTION = "/cgi-bin/luci/admin/beeconmini2/web_action"
PATH_UBUS = "/ubus"
# CSDP 直通端点：POST 紧凑 JSON → /tmp/bxplug.sock
PATH_CSDP = "/cgi-bin/luci/admin/beeconmini2/api"

# ubus 匿名会话 token（用于 session.login）
UBUS_NULL_TOKEN = "00000000000000000000000000000000"
# 路由器内置的 lua 命令入口（ubus file.exec 执行它来跑 action）
LOCAL_CMD_SCRIPT = "/usr/share/rtmgr/local_cmd.lua"

# 单次 HTTP 请求超时（秒）
TIMEOUT_SECONDS = 25.0

# ----------------------------------------------------------------------
# CSDP act 码（实测确认）
# ----------------------------------------------------------------------
ACT_AP_DETAILS = 31     # AP 管理/状态页：信道 / 功率档 / 端口速率 / 运行时长 / 分频段用户数
ACT_STA_GET = 34        # 无线终端明细：**唯一**能拿到「终端归属哪台 AP」的接口
ACT_KICK = 249          # 剔除终端（厂商原生，一次性断连，终端可重连）
ACT_ROAMING_READ = 248  # 漫游策略读

# ----------------------------------------------------------------------
# 本地 lua 命令 action（走 ubus file.exec → local_cmd.lua）
# ----------------------------------------------------------------------
ACTION_STATUS = "status"
ACTION_USERS = "getusers"
ACTION_PRODUCT = "getproductinfo"
ACTION_WAN_STATS = "wan_usb_stats"

# ----------------------------------------------------------------------
# 写操作：AP 定时重启（csdp.sys.rben/rbday/rbhour/rbminute）
# ----------------------------------------------------------------------
UCI_CSDP = "csdp"
RB_KEY_ENABLED = "rben"
RB_KEY_DAY = "rbday"
RB_KEY_HOUR = "rbhour"
RB_KEY_MINUTE = "rbminute"
RB_DEFAULT_DAY = 8      # 1-7 = 周一至周日，8 = 每天
RB_DEFAULT_HOUR = 0
RB_DEFAULT_MINUTE = 0

# ----------------------------------------------------------------------
# 写操作：AP **立即重启**（真实通道，2026-10-08 实机确认）
# ----------------------------------------------------------------------
# 厂商固件里的重启由 bxplug 守护进程的 CLI 模块提供，模块树实测为：
#
#     $ bxplug -m help
#       - urtm:[sm]
#       - csdp:[web] [rootap] [update_stas] [reboot] [factory] [nclog]
#               [dlog] [clog] [updl] [upok] [cfgmng]
#     $ bxplug -m "csdp:help"
#       reboot  -  reset button trigger reboot
#
# 即 ``bxplug -m "csdp:reboot"`` = 模拟「按下复位键」→ 给**全部纳管 AP**
# 下发重启（**AC 自身不重启**），stdout 回 ``OK``。
#
# ⚠️ 与 ``csdp.sys.rben`` 定时重启无关：
#   * 定时重启位由 csdp_reboot.c 实现，但它最终要执行
#     ``/usr/share/rtmgr/reboot %u``，而该脚本**不在插件包里**
#     （``opkg files beeconmini2-seed-ac2`` 的 228 个文件中没有它）
#     → 定时重启永远不可能生效；
#   * 本 CLI 通道**不依赖该脚本**，实测两次触发均有效（AP 运行时长归零）。
#
# ⚠️ 副作用（已知且可自愈）：触发时 bxplug 会打印内部断言
#   （``assert len failed`` / ``priv not init!``）并重连 AP，
#   三台 AP 掉线约 40–110 秒后自行回上线，AC 不受影响。
CMD_REBOOT_APS = 'bxplug -m "csdp:reboot"'

# 独立回显标记：ubus file.exec 的 stdout 可能整段丢失（见 client 里的说明），
# 所以「执行」与「确认」分开 —— 先跑 CLI，再用一条独立的 echo 探针判断通道是否正常。
REBOOT_ACK_MARKER = "__BEE_ACK__"
CMD_REBOOT_ACK = f"echo {REBOOT_ACK_MARKER}"

# ----------------------------------------------------------------------
# 写操作：**单台 AP** 任意 shell（含单台重启）—— cocmd 通道
# （2026-10-09 实机确认，三台 AP 上均验证通过）
# ----------------------------------------------------------------------
# 模块树（实测）：
#
#     $ bxplug -m "urtm:help"            → sm
#     $ bxplug -m "urtm:sm:help"         → comsg / edrv / wdrv
#     $ bxplug -m "urtm:sm:comsg:sm:help" → conn / coalive / cobs / cocmd
#     $ bxplug -m "urtm:sm:comsg:sm:cocmd:help"
#         cmd - handle shell cmd on APs,result will return to current console
#         res - result return to commander
#
# 参数拼法（把 handler 反汇编逆出来，2026-10-09）：
#
#     bxplug -m "urtm:sm:comsg:sm:cocmd:cmd:<18字符MAC><shell命令>"
#
# * ``cmd`` handler 先用 MAC 解析器（0x42d16c）吃掉**正好 18 个字符**
#   —— 每字节占 3 字符（``xx`` + 1 个分隔符，第 3 字符不校验），
#   所以 MAC 必须写成 ``50:b3:b4:3b:16:30:``（**结尾带冒号**，共 18 字符）。
#   大小写不敏感（内部走 strtol base=16）。
# * 剩下**全部**剩余文本即为下发到 AP 的 shell 命令（由 AP 侧
#   ``/usr/share/rtmgr/cocmd`` 脚本 ``eval`` 执行）；
# * 命令是否真的到了 AP，**不能**看 CLI 的退出码 —— 见下方 ⚠️。
#
# 实测证据链（2026-10-09）：
#   * ``...:cmd:50:b3:b4:3b:16:30:echo COCMD_PONG|nc 192.168.9.1 9991``
#     → 悦房 AP 主动回连 AC，AC 侧 listener 收到 ``COCMD_PONG``；
#   * ``...:cmd:50:b3:b4:3f:4e:3c:echo PING2`` → 乐房执行，stdout ``PING2``
#     经 ``cocmd:res`` 原样回显到当前控制台；
#   * ``...:cmd:50:b3:b4:3b:16:30:reboot`` → **悦房运行时长归零**，
#     乐房/客厅运行时长连续增长（单台定向成立）；AC 自身不重启；
#   * 不存在的 MAC（``aa:bb:cc:dd:ee:ff``）→ 静默无操作，rc=0，安全。
#
# ⚠️ **CLI 退出码不可信**：AP 重启时来不及回 ``cocmd:res``，CLI 会返回 **1**
#    （实测「重启成功」的那次就是 rc=1；而快速回结果的 ``echo`` 是 rc=0）。
#    rc=1 的含义是「本轮没收到回包」，**不是**「命令没发出去」。
#    判定成败只能靠**观察 AP**（掉线后 uptime 归零）。
CMD_AP_COCMD_FMT = 'bxplug -m "urtm:sm:comsg:sm:cocmd:cmd:{mac18}{command}"'
# AP 侧执行的重启命令（在 AP 上 eval，与 AC 无关）
AP_REBOOT_COMMAND = "reboot"
# MAC 参数在 CLI 里必须占的字符数（6 字节 × 3 字符）
AP_COCMD_MAC_LEN = 18


def build_ap_cocmd(mac: str, command: str) -> str:
    """拼「在单台 AP 上执行 shell」的 bxplug CLI 命令。

    :param mac: 目标 AP 的 MAC，``aa:bb:cc:dd:ee:ff`` / ``aa-bb-...`` 均可，
                大小写不限；内部会归一化成小写冒号形式并**补尾冒号**到 18 字符。
    :param command: 要在该 AP 上执行的 shell（AP 侧 ``eval``）。
    :raises ValueError: MAC 不是合法的 6 字节写法，或命令为空。
    """
    mac18 = ap_cocmd_mac(mac)
    cmd = (command or "").strip()
    if not cmd:
        raise ValueError("AP 命令不能为空")
    if "\n" in cmd or "\r" in cmd:
        raise ValueError("AP 命令不能包含换行（CLI 参数是单行）")
    return CMD_AP_COCMD_FMT.format(mac18=mac18, command=cmd)


def ap_cocmd_mac(mac: str) -> str:
    """把 MAC 归一化成 cocmd CLI 需要的 **18 字符**形式。

    ``50:B3:B4:3B:16:30`` → ``50:b3:b4:3b:16:30:``（补一个尾冒号）。

    ⚠️ 尾冒号不能省：handler 按「每字节 3 字符」硬吃 18 个字符，
    少一个字符就会把命令的第一个字符当成 MAC 分隔符吃掉。
    """
    raw = (mac or "").strip().lower().replace("-", ":")
    parts = raw.split(":")
    if len(parts) != 6 or any(len(p) != 2 for p in parts):
        raise ValueError(f"MAC 格式不合法：{mac!r}（示例 aa:bb:cc:dd:ee:ff）")
    if any(c not in "0123456789abcdef" for p in parts for c in p):
        raise ValueError(f"MAC 含非十六进制字符：{mac!r}")
    mac18 = ":".join(parts) + ":"
    assert len(mac18) == AP_COCMD_MAC_LEN, mac18
    return mac18

# ----------------------------------------------------------------------
# 取数策略：每个数据集一个来源
# ----------------------------------------------------------------------
# 来源写法：
#   ``action:<name>``   走 action 通道（ubus local_cmd 优先，失败回落 web_action）
#   ``csdp:<act>``      走 CSDP 直通端点（实时）
SOURCE_AC_PRODUCT = ("action:getproductinfo",)
SOURCE_AC_STATUS = ("action:status",)
SOURCE_USERS = ("action:getusers",)
SOURCE_WAN_STATS = ("action:wan_usb_stats",)
SOURCE_STA_LIST = ("csdp:34",)
SOURCE_ROAM_POLICY = ("csdp:248",)

# AP 清单**只来自 act:31**（实时明细），act:34 的归属信息用于兜底建单：
#   * act:31 给出「有哪些 AP」+ 型号/版本/端口/运行时长/分频段用户数；
#   * act:31 整条挂掉时，仍可由 act:34 的 s111/s112 建出 AP 条目，
#     保证终端的 via_device 能解析到父设备（否则终端会掉成顶级设备）。
#   任一来源失败都只降级、不报错。见 models._build_aps()。
SOURCE_AP_DETAILS = ("csdp:31",)

# 某些 act 需要附带固定参数（键顺序无关，但必须发紧凑 JSON）
ACT_PAYLOAD_EXTRA = {
    34: {"c1150": 0},
}

# ----------------------------------------------------------------------
# 枚举表
# ----------------------------------------------------------------------
# act:34 的 a078：频段码
BAND_MAP = {
    0: "2.4G",
    1: "2.4G 访客",
    2: "5G",
    4: "5G 访客",
}

# r03 / r13：功率档（0 最弱）
POWER_LEVELS = {
    0: "极低",
    1: "低",
    2: "中",
    3: "高",
}

# s07 / a041：端口速率码；255 表示自动协商
PORT_SPEED_MAP = {
    0: "10M",
    1: "100M",
    2: "1000M",
    3: "2500M",
}
PORT_SPEED_AUTO = 255

# 主机名占位符：命中则回退显示 MAC
PLACEHOLDER_HOSTNAMES = frozenset(
    {"", "--", "-", "*", "无", "unknown", "Unknown", "null", "N/A", "n/a"}
)

# 厂商基础设备（AC / AP 自身）的 MAC 前缀。
# 它们会出现在 getusers 里，统计「有线终端」时必须剔除。
INFRA_MAC_PREFIX = "50:b3:b4"
# act:31 的 aps[] 里 AC 主机自身用的名字
AC_HOSTNAME = "AC"

# 漫游策略的 dBm 存储偏移（前端存 实际值+95）
ROAM_RSSI_OFFSET = 95
