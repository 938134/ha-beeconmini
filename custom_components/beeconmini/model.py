"""BeeconMini AC 数据模型 —— AP / 无线终端 / AC 主机 / 漫游策略字段解码。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class BeeconAP:
    """一台被 AC 纳管的无线 AP。

    字段合并自两路数据源：
    - /tmp/json/apinfos 快照：在线状态、终端数、负载
    - act:31 AP 管理页：型号、版本、端口速率、运行时长、2.4G/5G 拆分
    """

    mac: str
    name: str = ""
    wired_clients: int = 0
    wireless_clients: int = 0
    load: int = 0
    configured: bool = False
    online: bool = False
    radios: int = 0
    ip: str | None = None

    # ---- act:31 扩展字段 ----
    ap_model: str = ""           # a02 硬件型号
    ap_version: str = ""         # a05 软件版本
    ap_sn: str = ""              # a07 序列号
    ap_ip: str = ""              # a04 AP IP
    channel_24: int | None = None  # r02
    channel_5: int | None = None   # r12
    power_24_code: int = -1        # r03
    power_5_code: int = -1         # r13
    port_speed_code: int = -1      # s07
    port_cap_code: int = -1        # a041
    port_plug: bool = False        # a0100
    uptime_seconds: int = 0        # a01
    users_24: int = 0              # a056
    users_5: int = 0               # a057

    @property
    def total_clients(self) -> int:
        return self.wired_clients + self.wireless_clients

    @property
    def display_name(self) -> str:
        return self.name or self.mac

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> BeeconAP:
        online_code = _int(raw.get("s00"))
        return cls(
            mac=str(raw.get("a00", "")).upper(),
            name=str(raw.get("c10") or "").strip(),
            wired_clients=_int(raw.get("r03")),
            wireless_clients=_int(raw.get("r13")),
            load=_int(raw.get("r02")),
            configured=_int(raw.get("c111")) == 1,
            online=online_code == 0,
            radios=_int(raw.get("s010")),
        )

    def update_from_details(self, raw: dict[str, Any]) -> None:
        """用 act:31 数据补充字段（与 apinfos 同名键语义不同，分字段写入）。"""
        self.ap_model = str(raw.get("a02") or "").strip()
        self.ap_version = str(raw.get("a05") or "").strip()
        self.ap_sn = str(raw.get("a07") or "").strip()
        self.ap_ip = str(raw.get("a04") or "").strip()
        self.channel_24 = _int_or_none(raw.get("r02"))
        self.channel_5 = _int_or_none(raw.get("r12"))
        self.power_24_code = _int(raw.get("r03"))
        self.power_5_code = _int(raw.get("r13"))
        self.port_speed_code = _int(raw.get("s07"))
        self.port_cap_code = _int(raw.get("a041"))
        self.port_plug = _int(raw.get("a0100")) != 0
        self.uptime_seconds = _int(raw.get("a01"))
        self.users_24 = _int(raw.get("a056"))
        self.users_5 = _int(raw.get("a057"))


BAND_MAP = {
    0: "2.4G",
    1: "2.4G 访客",
    2: "5G",
    4: "5G 访客",
}


@dataclass(slots=True)
class BeeconSta:
    """一台**无线**接入终端（act:34，含所属 AP 与射频链路信息）。"""

    mac: str
    ip: str
    hostname: str
    ap_mac: str | None = None
    ap_name: str | None = None
    ap_model: str | None = None
    band: str = "--"
    band_code: int = -1
    channel: int | None = None
    rssi: int | None = None
    phy_mode: str | None = None
    tx_rate: int | None = None
    rx_rate: int | None = None
    mlo: bool = False

    @property
    def display_name(self) -> str:
        return self.hostname if self.hostname not in ("", "--") else self.mac

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> BeeconSta:
        band_code = _int(raw.get("a078"))
        return cls(
            mac=str(raw.get("s10", "")).upper(),
            ip=str(raw.get("s11", "")),
            hostname=str(raw.get("s12") or "").strip(),
            ap_mac=str(raw.get("s111") or "").strip() or None,
            ap_name=str(raw.get("s112") or "").strip() or None,
            ap_model=str(raw.get("s114") or "").strip() or None,
            band=BAND_MAP.get(band_code, "--"),
            band_code=band_code,
            channel=_int_or_none(raw.get("a079")),
            rssi=_int_or_none(raw.get("a072")),
            phy_mode=str(raw.get("a073") or "").strip() or None,
            tx_rate=_int_or_none(raw.get("a074")),
            rx_rate=_int_or_none(raw.get("a075")),
            mlo=bool(raw.get("exsta_list")),
        )


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
    def from_raw(cls, raw: dict[str, Any]) -> BeeconDeviceInfo:
        temp_raw = raw.get("cpu_temp")
        cpu_temp: float | None = None
        if temp_raw not in (None, ""):
            try:
                cpu_temp = round(float(temp_raw) / 1000.0, 1)
            except (TypeError, ValueError):
                cpu_temp = None
        return cls(
            model=str(raw.get("model") or ""),
            version=str(raw.get("version") or ""),
            sn=str(raw.get("snum") or ""),
            mac=str(raw.get("mac") or "").upper(),
            cpu_temp=cpu_temp,
            cpu_usage=_int_or_none(raw.get("cpu_usage")),
            mem_usage=_int_or_none(raw.get("mem_usage")),
            conn_num=_int_or_none(raw.get("conn_num")),
        )


@dataclass(slots=True)
class BeeconUser:
    """一台在线终端（来自 DHCP/ARP 视角，getusers）。"""

    mac: str
    ip: str
    hostname: str
    gateway: str

    @property
    def display_name(self) -> str:
        return self.hostname if self.hostname not in ("", "--") else self.mac

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> BeeconUser:
        return cls(
            mac=str(raw.get("mac", "")).upper(),
            ip=str(raw.get("ip", "")),
            hostname=str(raw.get("hostname") or "").strip(),
            gateway=str(raw.get("gateway", "")),
        )


@dataclass(slots=True)
class RPolicy:
    """漫游策略（只读）。

    前端存储值 = 实际 dBm + 95，展示时 -95 还原。
    值为 0 表示""未设置""，不换算。
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
    def from_raw(cls, raw: dict[str, Any]) -> RPolicy:
        def _roam(value: Any) -> int | None:
            v = _int_or_none(value)
            return None if v is None or v == 0 else v - 95

        return cls(
            r24_roaming_trigger_dbm=_roam(raw.get("rp2")),
            r5_roaming_trigger_dbm=_roam(raw.get("rp3")),
            r24_eviction_threshold_dbm=_roam(raw.get("rp4")),
            r5_eviction_threshold_dbm=_roam(raw.get("rp5")),
            load_balance_rssi_dbm=_roam(raw.get("rp6")),
            r24_max_clients_per_radio=_int_or_none(raw.get("rp11")),
            r5_max_clients_per_radio=_int_or_none(raw.get("rp12")),
            r24_eviction_enabled=_int(raw.get("rp16")) == 1,
            r5_eviction_enabled=_int(raw.get("rp17")) == 1,
        )


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

    def ap_by_mac(self, mac: str) -> BeeconAP | None:
        for ap in self.aps:
            if ap.mac == mac:
                return ap
        return None

    def sta_by_mac(self, mac: str) -> BeeconSta | None:
        target = mac.upper()
        for sta in self.stas:
            if sta.mac == target:
                return sta
        return None

    def stas_of_ap(self, ap_mac: str) -> list[BeeconSta]:
        target = ap_mac.upper()
        return [s for s in self.stas if (s.ap_mac or "").upper() == target]

    @property
    def wireless_client_count(self) -> int:
        if self.stas:
            return len(self.stas)
        return sum(ap.wireless_clients for ap in self.aps)

    @property
    def wired_client_count(self) -> int:
        return sum(ap.wired_clients for ap in self.aps)


def build_state(
    product: dict[str, Any],
    status: dict[str, Any],
    users_raw: list[dict[str, Any]],
    apinfos_raw: dict[str, Any] | None,
    wan_stats: dict[str, Any] | None,
    stas_raw: list[dict[str, Any]] | None = None,
    ap_details_raw: list[dict[str, Any]] | None = None,
    rpolicys_raw: dict[str, Any] | None = None,
) -> ACState:
    """把各通道原始数据组装成统一的 ACState。"""
    aps = [
        BeeconAP.from_raw(item)
        for item in (apinfos_raw or {}).get("aps", [])
        if isinstance(item, dict)
    ]

    # 用 act:31 数据补充 AP 字段
    if ap_details_raw:
        details_by_mac: dict[str, dict[str, Any]] = {}
        for item in ap_details_raw:
            if isinstance(item, dict):
                mac = str(item.get("a00", "")).upper()
                if mac:
                    details_by_mac[mac] = item
        for ap in aps:
            detail = details_by_mac.get(ap.mac)
            if detail:
                ap.update_from_details(detail)

    users = [BeeconUser.from_raw(u) for u in users_raw if isinstance(u, dict)]
    stas = [BeeconSta.from_raw(s) for s in (stas_raw or []) if isinstance(s, dict)]

    lans = (status or {}).get("lans", {}) or {}
    lan_block = lans.get("lan", {}) if isinstance(lans, dict) else {}
    stats = wan_stats or {}

    # 漫游策略
    rpolicy: RPolicy | None = None
    if rpolicys_raw:
        rp_list = rpolicys_raw.get("rpolicy", [])
        if isinstance(rp_list, list) and rp_list:
            rpolicy = RPolicy.from_raw(rp_list[0])

    return ACState(
        device=BeeconDeviceInfo.from_raw(product or {}),
        aps=aps,
        users=users,
        stas=stas,
        rpolicy=rpolicy,
        wan_rx_bytes=_int(stats.get("wan_rxbyte")),
        wan_tx_bytes=_int(stats.get("wan_txbyte")),
        lan_ip=str(lan_block.get("lanipaddr") or ""),
    )


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
