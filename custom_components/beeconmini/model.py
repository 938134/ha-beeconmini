"""BeeconMini AC 数据模型 —— AP / 无线终端 / AC 主机字段解码。

字段名来自对 `/tmp/json/apinfos` 快照与 `act:34` 接口的实测逆向。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class BeeconAP:
    """一台被 AC 纳管的无线 AP。"""

    mac: str
    name: str = ""
    wired_clients: int = 0
    wireless_clients: int = 0
    load: int = 0
    configured: bool = False
    online: bool = False
    radios: int = 0
    ip: str | None = None

    @property
    def total_clients(self) -> int:
        return self.wired_clients + self.wireless_clients

    @property
    def display_name(self) -> str:
        return self.name or self.mac

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> BeeconAP:
        # 实测：s00==0 表示在线（反直觉）
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


# a078 频段码 → 文案（实测自厂商前端「用户状态」页）
BAND_MAP = {
    0: "2.4G",
    1: "2.4G 访客",
    2: "5G",
    4: "5G 访客",
}


@dataclass(slots=True)
class BeeconSta:
    """一台**无线**接入终端（act:34，含所属 AP 与射频链路信息）。"""

    mac: str                       # s10
    ip: str                        # s11
    hostname: str                  # s12
    ap_mac: str | None = None      # s111：所连 AP 的 radio MAC
    ap_name: str | None = None     # s112：所连 AP 名称
    ap_model: str | None = None    # s114：AP 型号
    band: str = "--"               # a078 解码
    band_code: int = -1
    channel: int | None = None     # a079
    rssi: int | None = None        # a072
    phy_mode: str | None = None    # a073
    tx_rate: int | None = None     # a074（Mbps）
    rx_rate: int | None = None     # a075（Mbps）
    mlo: bool = False              # 有 exsta_list = MLO 双链路

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
        # cpu_temp 单位是 0.001°C（如 72104 → 72.1°C）
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
class ACState:
    """一次轮询得到的完整 AC 状态快照。"""

    device: BeeconDeviceInfo
    aps: list[BeeconAP] = field(default_factory=list)
    users: list[BeeconUser] = field(default_factory=list)
    stas: list[BeeconSta] = field(default_factory=list)
    wan_rx_bytes: int = 0
    wan_tx_bytes: int = 0
    lan_ip: str = ""

    # 快速按 MAC 查 AP（避免每次属性访问都扫列表）
    def ap_by_mac(self, mac: str) -> BeeconAP | None:
        for ap in self.aps:
            if ap.mac == mac:
                return ap
        return None

    def stas_of_ap(self, ap_mac: str) -> list[BeeconSta]:
        """某台 AP 下的无线终端（MAC 大小写不敏感）。"""
        target = ap_mac.upper()
        return [s for s in self.stas if (s.ap_mac or "").upper() == target]

    @property
    def wireless_client_count(self) -> int:
        """无线终端总数（无线终端来自 act:34，比 AP 报表累加更准）。"""
        if self.stas:
            return len(self.stas)
        return sum(ap.wireless_clients for ap in self.aps)

    @property
    def wired_client_count(self) -> int:
        """有线终端数（AP 报表里的 wired_clients 累加）。"""
        return sum(ap.wired_clients for ap in self.aps)


def build_state(
    product: dict[str, Any],
    status: dict[str, Any],
    users_raw: list[dict[str, Any]],
    apinfos_raw: dict[str, Any] | None,
    wan_stats: dict[str, Any] | None,
    stas_raw: list[dict[str, Any]] | None = None,
) -> ACState:
    """把各通道原始数据组装成统一的 ACState。"""
    aps = [
        BeeconAP.from_raw(item)
        for item in (apinfos_raw or {}).get("aps", [])
        if isinstance(item, dict)
    ]
    users = [BeeconUser.from_raw(u) for u in users_raw if isinstance(u, dict)]
    stas = [BeeconSta.from_raw(s) for s in (stas_raw or []) if isinstance(s, dict)]

    lans = (status or {}).get("lans", {}) or {}
    lan_block = lans.get("lan", {}) if isinstance(lans, dict) else {}
    stats = wan_stats or {}

    return ACState(
        device=BeeconDeviceInfo.from_raw(product or {}),
        aps=aps,
        users=users,
        stas=stas,
        wan_rx_bytes=_int(stats.get("wan_rxbyte")),
        wan_tx_bytes=_int(stats.get("wan_txbyte")),
        lan_ip=str(lan_block.get("lanipaddr") or ""),
    )


# ----------------------------------------------------------------------
# 小工具
# ----------------------------------------------------------------------
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
