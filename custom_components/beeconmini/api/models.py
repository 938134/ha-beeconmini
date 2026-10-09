"""数据模型与字段解码（L3）。

这里把「设备返回的原始 dict」翻译成有意义的对象，是**唯一**知道
``a00`` / ``s112`` / ``r03`` 这些厂商字段含义的地方。

⚠️ 同名键陷阱（务必注意）
    ``r02`` / ``r12`` / ``r03`` / ``r13`` 是 2.4G/5G 信道、2.4G/5G 功率档。
    它们**不是**负载、也不是有线/无线终端数（2026-10-08 实机纠错）。

    ``c111`` 在 ``act:31`` 里是**指示灯**开关 —— 不要拿它当「已纳管」
    （旧版曾把 apinfos 快照的 c111 当纳管标记，v1.3.3 已连同快照通道一起删除）。

📌 v1.3.3：AP 数据**只来自 act:31**，不再读 ``/tmp/json/apinfos`` 快照
（那批文件数月不更新、字段少、信道值 6 个里 4 个与实际不符，见 :mod:`protocol`）。
act:31 整条挂掉时，由 act:34 的归属信息（``s111`` / ``s112``）兜底建出 AP 条目。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import protocol as P
from .parsers import (
    cpu_temp_c,
    display_hostname,
    first_dict,
    format_band,
    format_port_speed,
    format_power_level,
    format_uptime,
    is_infra_mac,
    normalize_mac,
    rssi_dbm,
    to_dbm,
    to_int,
    to_int_or_none,
)


def _iter_dicts(value: Any) -> Iterable[dict[str, Any]]:
    """遍历一个「应该是 dict 列表」的字段，跳过脏数据。"""
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                yield item


def _macs(items: Iterable[Any]) -> set[str]:
    """取一组对象的 MAC（归一化），用于集合运算。"""
    out: set[str] = set()
    for item in items:
        mac = getattr(item, "mac", None)
        if mac:
            out.add(normalize_mac(str(mac)))
    return out


def _is_ac_self(raw: dict[str, Any]) -> bool:
    """``act:31`` 的 ``aps[]`` 里**同时包含 AC 主机自己**，需要识别并排除。"""
    return str(raw.get("c10") or "").strip().upper() == P.AC_HOSTNAME.upper()


# ----------------------------------------------------------------------
# AP
# ----------------------------------------------------------------------
@dataclass(slots=True)
class BeeconAP:
    """一台被 AC 纳管的无线 AP。

    字段来自 **act:31**（AP 管理/状态页明细）：

    * :meth:`from_raw` 读基础字段（在线 / 名称 / 信道 / 功率档等）；
    * :meth:`merge_details` 补型号 / 版本 / 序列号 / IP / 端口 / 运行时长 /
      分频段用户数。

    两个方法都只认 act:31 的字段语义。少数 AP 条目由 act:34 兜底建出
    （act:31 整条挂掉时），此时只有 ``mac`` / ``name`` 有效，其余保持默认。
    """

    mac: str
    name: str = ""

    # ---- 在线信息 ----
    online: bool = False
    radios: int = 0

    # ---- 射频 ----
    channel_24: int | None = None      # r02
    channel_5: int | None = None       # r12
    power_24_code: int | None = None   # r03 → 极低/低/中/高
    power_5_code: int | None = None    # r13 → 极低/低/中/高

    # ---- 明细 ----
    ap_model: str = ""                 # a02
    ap_version: str = ""               # a05
    ap_sn: str = ""                    # a07
    ap_ip: str = ""                    # a04
    port_speed_code: int | None = None  # s07
    port_cap_code: int | None = None    # a041
    port_plug: bool | None = None       # a0100
    uptime_seconds: int = 0             # a01
    users_24: int | None = None         # a056 ← 2.4G 在线终端数（权威口径）
    users_5: int | None = None          # a057 ← 5G 在线终端数（权威口径）

    led_state: bool | None = None       # c111 → 指示灯（**不是**「已纳管」）

    # ---- 回退口径：act:34 按归属统计出的条数 ----
    sta_count_measured: int | None = None

    # ------------------------------------------------------------------
    # 展示
    # ------------------------------------------------------------------
    @property
    def display_name(self) -> str:
        return self.name or self.mac

    @property
    def ip(self) -> str:
        """兼容旧字段名（历史版本里叫 ``ip``）。"""
        return self.ap_ip

    @property
    def sta_count(self) -> int:
        """接入的无线终端数（**与 AP 设备页列出的终端一一对应**）。

        口径优先级（2026-10-08 实机纠偏）：

        1. ``sta_count_measured`` —— act:34 按归属实测的条数。它**恰好等于**
           HA 里挂在这台 AP 之下的终端设备数，所以优先用它，
           否则「计数」与「设备清单」会互相打脸（实测客厅 AP 自报 14、
           实际只归属到 12；AC 侧总数也会对不上）。
        2. ``a056 + a057`` —— act:31 的 AP 自报数，act:34 不可用时兜底。

        **AP 下没有「有线终端」这一级** —— 有线终端直连 AC 的 LAN 口。
        """
        if self.sta_count_measured is not None:
            return self.sta_count_measured
        if self.users_24 is not None and self.users_5 is not None:
            return self.users_24 + self.users_5
        return 0

    @property
    def ap_reported_count(self) -> int | None:
        """AP 自报的无线终端数（``a056 + a057``，仅供对照，见 :attr:`sta_count`）。"""
        if self.users_24 is None or self.users_5 is None:
            return None
        return self.users_24 + self.users_5

    # 向后兼容：旧代码用 total_clients 表示「这台 AP 的终端数」
    @property
    def total_clients(self) -> int:
        return self.sta_count

    @property
    def channel_text(self) -> str:
        c24 = str(self.channel_24) if self.channel_24 else "?"
        c5 = str(self.channel_5) if self.channel_5 else "?"
        return f"2.4G:{c24} 5G:{c5}"

    @property
    def power_text(self) -> str:
        p24 = format_power_level(self.power_24_code) if self.power_24_code is not None else "?"
        p5 = format_power_level(self.power_5_code) if self.power_5_code is not None else "?"
        return f"2.4G:{p24} 5G:{p5}"

    @property
    def port_speed_text(self) -> str:
        """端口速率展示。

        优先「协商速率」（``s07``）；协商不到时退到「端口能力」（``a041``）——
        实测部分型号（Mini7-5100C）的 ``s07`` 恒为 null，没有回退就永远
        显示「未知」。
        """
        if self.port_speed_code is not None:
            return format_port_speed(self.port_speed_code)
        if self.port_cap_code is not None:
            return f"{format_port_speed(self.port_cap_code)}（端口能力）"
        return format_port_speed(None)

    @property
    def uptime_text(self) -> str:
        return format_uptime(self.uptime_seconds)

    @property
    def led_on(self) -> bool | None:
        """指示灯开关（act:31 的 ``c111`` —— 注意**不是**「已纳管」）。"""
        return self.led_state

    # ------------------------------------------------------------------
    # 构造
    # ------------------------------------------------------------------
    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "BeeconAP":
        """从 **act:31** 条目建立 AP（读全部可用字段）。

        act:31 是 AP 数据的唯一来源；条目里 ``a00`` / ``c10`` 必有，
        其余字段按「有值才记」处理。
        """
        ap = cls(
            mac=str(raw.get("a00", "")).upper(),
            name=str(raw.get("c10") or "").strip(),
        )
        ap.merge_details(raw)
        return ap

    def merge_details(self, raw: dict[str, Any]) -> None:
        """把一条 **act:31** 条目的字段合并进来。

        **取值一律「有值才覆盖」**：act:31 里部分 AP 的 ``s07`` 为 null
        （实测乐房/客厅如此），若无条件覆盖会把已有的有效值抹掉。

        ``c111`` 只写进 :attr:`led_state`（指示灯）—— 不要再赋予
        「是否已纳管」的含义（那是已删除的快照字段语义）。
        """
        self.ap_model = str(raw.get("a02") or "").strip() or self.ap_model
        self.ap_version = str(raw.get("a05") or "").strip() or self.ap_version
        self.ap_sn = str(raw.get("a07") or "").strip() or self.ap_sn
        ap_ip = str(raw.get("a04") or "").strip()
        if ap_ip:
            self.ap_ip = ap_ip
        if "c10" in raw and str(raw.get("c10") or "").strip():
            self.name = str(raw["c10"]).strip()
        if "s00" in raw:
            self.online = to_int(raw.get("s00")) == 0
        if "s010" in raw:
            self.radios = to_int(raw.get("s010"))
        if "c111" in raw:
            self.led_state = to_int(raw.get("c111")) == 1

        # 射频：有值才覆盖
        for attr, key in (
            ("channel_24", "r02"),
            ("channel_5", "r12"),
            ("power_24_code", "r03"),
            ("power_5_code", "r13"),
            ("port_speed_code", "s07"),
            ("port_cap_code", "a041"),
        ):
            value = to_int_or_none(raw.get(key))
            if value is not None:
                setattr(self, attr, value)

        raw_plug = to_int_or_none(raw.get("a0100"))
        if raw_plug is not None:
            self.port_plug = raw_plug != 0

        uptime = to_int(raw.get("a01"))
        if uptime:
            self.uptime_seconds = uptime

        users_24 = to_int_or_none(raw.get("a056"))
        if users_24 is not None:
            self.users_24 = users_24
        users_5 = to_int_or_none(raw.get("a057"))
        if users_5 is not None:
            self.users_5 = users_5


# ----------------------------------------------------------------------
# 无线终端
# ----------------------------------------------------------------------
@dataclass(slots=True)
class BeeconSta:
    """一台**无线**接入终端（``act:34``，含所属 AP 与射频链路信息）。"""

    mac: str
    ip: str = ""
    hostname: str = ""
    ap_mac: str | None = None      # s111 AP 射频 MAC
    ap_name: str | None = None     # s112 AP 名称（与 act:31 的 c10 一致）
    ap_model: str | None = None    # s114
    band: str = "--"
    band_code: int = -1
    channel: int | None = None     # a079
    rssi: int | None = None        # a072（固件给正值幅度，已转成负 dBm）
    phy_mode: str | None = None    # a073
    tx_rate: int | None = None     # a074
    rx_rate: int | None = None     # a075
    mlo: bool = False

    # ---- MLO 次链路（exsta_list）----
    channel_2: int | None = None
    rssi_2: int | None = None
    phy_mode_2: str | None = None
    tx_rate_2: int | None = None
    rx_rate_2: int | None = None

    @property
    def display_name(self) -> str:
        return display_hostname(self.hostname, self.mac)

    @property
    def wireless_info_text(self) -> str:
        """``客厅/5G/信道36/-52dBm`` 之类的一行摘要。"""
        parts = [self.ap_name or "--", self.band]
        if self.channel:
            parts.append(f"信道{self.channel}")
        if self.rssi is not None:
            parts.append(f"{self.rssi}dBm")
        if self.mlo:
            parts.append("MLO")
        return "/".join(parts)

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "BeeconSta":
        from .parsers import extract_exsta

        band_code = to_int(raw.get("a078"))
        exsta = extract_exsta(raw.get("exsta_list"))
        return cls(
            mac=str(raw.get("s10", "")).upper(),
            ip=str(raw.get("s11", "")),
            hostname=str(raw.get("s12") or "").strip(),
            ap_mac=str(raw.get("s111") or "").strip().upper() or None,
            ap_name=str(raw.get("s112") or "").strip() or None,
            ap_model=str(raw.get("s114") or "").strip() or None,
            band=format_band(band_code),
            band_code=band_code,
            channel=to_int_or_none(raw.get("a079")),
            rssi=rssi_dbm(raw.get("a072")),
            phy_mode=str(raw.get("a073") or "").strip() or None,
            tx_rate=to_int_or_none(raw.get("a074")),
            rx_rate=to_int_or_none(raw.get("a075")),
            mlo=exsta is not None,
            channel_2=to_int_or_none(exsta.get("a079")) if exsta else None,
            rssi_2=rssi_dbm(exsta.get("a072")) if exsta else None,
            phy_mode_2=str(exsta.get("a073") or "").strip() or None if exsta else None,
            tx_rate_2=to_int_or_none(exsta.get("a074")) if exsta else None,
            rx_rate_2=to_int_or_none(exsta.get("a075")) if exsta else None,
        )


# ----------------------------------------------------------------------
# AC 主机 / 终端
# ----------------------------------------------------------------------
@dataclass(slots=True)
class BeeconDeviceInfo:
    """AC 主机（路由器本体）信息。"""

    model: str = ""
    version: str = ""
    sn: str = ""
    mac: str = ""
    cpu_temp: float | None = None
    cpu_usage: int | None = None
    mem_usage: int | None = None
    conn_num: int | None = None

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "BeeconDeviceInfo":
        return cls(
            model=str(raw.get("model") or ""),
            version=str(raw.get("version") or ""),
            sn=str(raw.get("snum") or ""),
            mac=str(raw.get("mac") or "").upper(),
            cpu_temp=cpu_temp_c(raw.get("cpu_temp")),
            cpu_usage=to_int_or_none(raw.get("cpu_usage")),
            mem_usage=to_int_or_none(raw.get("mem_usage")),
            conn_num=to_int_or_none(raw.get("conn_num")),
        )


@dataclass(slots=True)
class BeeconUser:
    """``getusers`` 的一条记录。

    ⚠️ ``getusers`` **不是设备表**：同一 MAC 会出现多条（含 DHCP 历史租约），
    统计时必须先去重，见 :meth:`ACState.wired_users`。
    """

    mac: str
    ip: str = ""
    hostname: str = ""
    gateway: str = ""

    @property
    def display_name(self) -> str:
        return display_hostname(self.hostname, self.mac)

    @property
    def is_infra(self) -> bool:
        """是厂商基础设备（AC / AP 自身）吗。"""
        return is_infra_mac(self.mac)

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "BeeconUser":
        return cls(
            mac=str(raw.get("mac", "")).upper(),
            ip=str(raw.get("ip", "")),
            hostname=str(raw.get("hostname") or "").strip(),
            gateway=str(raw.get("gateway", "")),
        )


# ----------------------------------------------------------------------
# 漫游策略
# ----------------------------------------------------------------------
@dataclass(slots=True)
class RPolicy:
    """漫游策略（只读）。

    前端存储值 = 实际 dBm + 95（0 表示「未设置」，不换算）。
    """

    r24_roaming_trigger_dbm: int | None = None
    r5_roaming_trigger_dbm: int | None = None
    r24_eviction_threshold_dbm: int | None = None
    r5_eviction_threshold_dbm: int | None = None
    load_balance_rssi_dbm: int | None = None
    r24_max_clients_per_radio: int | None = None
    r5_max_clients_per_radio: int | None = None
    r24_eviction_enabled: bool = False
    r5_eviction_enabled: bool = False

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "RPolicy":
        return cls(
            r24_roaming_trigger_dbm=to_dbm(raw.get("rp2")),
            r5_roaming_trigger_dbm=to_dbm(raw.get("rp3")),
            r24_eviction_threshold_dbm=to_dbm(raw.get("rp4")),
            r5_eviction_threshold_dbm=to_dbm(raw.get("rp5")),
            load_balance_rssi_dbm=to_dbm(raw.get("rp6")),
            r24_max_clients_per_radio=to_int_or_none(raw.get("rp11")),
            r5_max_clients_per_radio=to_int_or_none(raw.get("rp12")),
            r24_eviction_enabled=to_int(raw.get("rp16")) == 1,
            r5_eviction_enabled=to_int(raw.get("rp17")) == 1,
        )


# ----------------------------------------------------------------------
# 汇总状态
# ----------------------------------------------------------------------
@dataclass(slots=True)
class ACState:
    """一次轮询得到的完整 AC 状态快照。"""

    device: BeeconDeviceInfo
    aps: list[BeeconAP] = field(default_factory=list)
    users: list[BeeconUser] = field(default_factory=list)
    stas: list[BeeconSta] = field(default_factory=list)
    rpolicy: RPolicy | None = None
    wan_rx_bytes: int = 0
    wan_tx_bytes: int = 0
    lan_ip: str = ""
    # 降级记录：数据集名 → 失败原因（实时取数失败并退到快照时也会记一条）
    errors: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # 查找
    # ------------------------------------------------------------------
    def ap_by_mac(self, mac: str) -> BeeconAP | None:
        target = normalize_mac(mac)
        for ap in self.aps:
            if normalize_mac(ap.mac) == target:
                return ap
        return None

    def sta_by_mac(self, mac: str) -> BeeconSta | None:
        target = normalize_mac(mac)
        for sta in self.stas:
            if normalize_mac(sta.mac) == target:
                return sta
        return None

    def stas_of_ap(self, ap_mac: str) -> list[BeeconSta]:
        """某台 AP 下的无线终端。

        ``s111`` 是 AP 的**射频** MAC（与 ``a00`` 不一定相同），所以优先用
        ``s112`` AP 名称匹配，再用 MAC 精确匹配兜底。
        """
        ap = self.ap_by_mac(ap_mac)
        if ap is None:
            return []
        target = normalize_mac(ap.mac)
        out: list[BeeconSta] = []
        for sta in self.stas:
            by_name = bool(ap.name) and sta.ap_name == ap.name
            by_mac = bool(sta.ap_mac) and normalize_mac(sta.ap_mac) == target
            if by_name or by_mac:
                out.append(sta)
        return out

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------
    @property
    def ap_online_count(self) -> int:
        return sum(1 for ap in self.aps if ap.online)

    @property
    def wireless_client_count(self) -> int:
        """无线终端总数：优先 act:34 实测条数，退到各 AP 的 a056+a057。"""
        if self.stas:
            return len(self.stas)
        return sum(ap.sta_count for ap in self.aps)

    def wired_users(self) -> list[BeeconUser]:
        """有线终端：``getusers`` 去重后，剔除无线终端与基础设施设备。

        * ``getusers`` 同一 MAC 会有多条（历史租约），先按 MAC 去重；
        * 去掉已经出现在 ``act:34`` 里的无线终端；
        * 去掉 AC / AP 自身（按已知 MAC 与厂商 OUI 前缀双重判断）。
        """
        wireless = _macs(self.stas)
        infra = _macs(self.aps) | {normalize_mac(m) for m in (s.ap_mac for s in self.stas) if m}
        if self.device.mac:
            infra.add(normalize_mac(self.device.mac))
        infra.discard("")

        seen: set[str] = set()
        out: list[BeeconUser] = []
        for user in self.users:
            mac = normalize_mac(user.mac)
            if not mac or mac in seen or mac in wireless or mac in infra:
                continue
            if is_infra_mac(mac):
                continue
            seen.add(mac)
            out.append(user)
        return out

    @property
    def wired_client_count(self) -> int:
        return len(self.wired_users())

    @property
    def unique_user_count(self) -> int:
        """``getusers`` 去重后的条数。"""
        return len({normalize_mac(u.mac) for u in self.users if u.mac})


# ----------------------------------------------------------------------
# 组装
# ----------------------------------------------------------------------
def _build_aps(
    ap_details_raw: list[dict[str, Any]] | None,
    stas: list[BeeconSta],
) -> list[BeeconAP]:
    """AP 清单：**只由 act:31 决定**，act:34 的归属信息用于兜底建单。

    1. 遍历 ``act:31`` 的 ``aps[]``（排除 AC 主机自身那一行）建立 AP；
    2. act:31 整条挂掉时，用 ``act:34`` 的 ``s111``（AP 射频 MAC）/ ``s112``
       （AP 名）建出**最小 AP 条目** —— 这一步不是为了显示，而是为了保证
       终端的 ``via_device`` 能解析到父设备：HA 2026.9.4 解析不到父设备时
       只记一条日志并丢链，终端会掉成顶级设备。
    3. 用 act:34 的归属条数填 ``sta_count_measured``（``a056+a057`` 的回退口径）。
    """
    base: dict[str, BeeconAP] = {}
    order: list[str] = []

    for item in _iter_dicts(ap_details_raw):
        mac = str(item.get("a00", "")).upper()
        if not mac or _is_ac_self(item):
            continue        # act:31 的 aps[] 含 AC 主机自身，排除
        ap = base.get(mac)
        if ap is None:
            ap = BeeconAP.from_raw(item)
            base[mac] = ap
            order.append(mac)
        else:
            ap.merge_details(item)

    # act:34 兜底建单（保证终端的父设备可解析）
    for sta in stas:
        mac = (sta.ap_mac or "").upper()
        if not mac or mac in base:
            continue
        base[mac] = BeeconAP(mac=mac, name=sta.ap_name or "")
        order.append(mac)

    # act:34 归属条数 → 作为 a056/a057 缺失时的回退口径
    measured = Counter(s.ap_name for s in stas if s.ap_name)
    for mac in order:
        ap = base[mac]
        if ap.name and ap.name in measured:
            ap.sta_count_measured = measured[ap.name]

    return [base[mac] for mac in order]


def build_state(
    product: dict[str, Any],
    status: dict[str, Any],
    users_raw: list[dict[str, Any]],
    wan_stats: dict[str, Any] | None,
    stas_raw: list[dict[str, Any]] | None = None,
    ap_details_raw: list[dict[str, Any]] | None = None,
    rpolicys_raw: dict[str, Any] | None = None,
    errors: dict[str, str] | None = None,
) -> ACState:
    """把各通道原始数据组装成统一的 :class:`ACState`。"""
    stas = [
        BeeconSta.from_raw(item)
        for item in _iter_dicts(stas_raw)
    ]
    # 同一终端瞬时可能被两路报文同时带上，按 MAC 去重（保留首条）
    deduped: dict[str, BeeconSta] = {}
    for sta in stas:
        if sta.mac and sta.mac not in deduped:
            deduped[sta.mac] = sta
    stas = list(deduped.values())

    users = [BeeconUser.from_raw(u) for u in _iter_dicts(users_raw)]

    lans = (status or {}).get("lans", {}) or {}
    lan_block = lans.get("lan", {}) if isinstance(lans, dict) else {}
    stats = wan_stats or {}

    rpolicy: RPolicy | None = None
    if rpolicys_raw:
        raw_policy = first_dict(rpolicys_raw.get("rpolicy"))
        if raw_policy is None and "rp2" in rpolicys_raw:
            raw_policy = rpolicys_raw
        if raw_policy is not None:
            rpolicy = RPolicy.from_raw(raw_policy)

    return ACState(
        device=BeeconDeviceInfo.from_raw(product or {}),
        aps=_build_aps(ap_details_raw, stas),
        users=users,
        stas=stas,
        rpolicy=rpolicy,
        wan_rx_bytes=to_int(stats.get("wan_rxbyte")),
        wan_tx_bytes=to_int(stats.get("wan_txbyte")),
        lan_ip=str(lan_block.get("lanipaddr") or ""),
        errors=dict(errors or {}),
    )
