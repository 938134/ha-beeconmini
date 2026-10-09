"""原始响应 → 语义值（L2）。

**纯函数层**：只做文本解析与字段解码，不知道数据从哪来
（不 import transport / models），因此可以独立单测。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from . import protocol as P

_LOGGER = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# 数值工具
# ----------------------------------------------------------------------
def to_int(value: Any) -> int:
    """任意值 → int，失败给 0。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def to_int_or_none(value: Any) -> int | None:
    """任意值 → int | None，空串/None 一律 None。"""
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def cpu_temp_c(value: Any) -> float | None:
    """``cpu_temp`` 原始单位是 **0.001°C**（72104 → 72.1°C）。"""
    if value in (None, ""):
        return None
    try:
        return round(float(value) / 1000.0, 1)
    except (TypeError, ValueError):
        return None


def rssi_dbm(value: Any) -> int | None:
    """终端信号强度 → 标准负 dBm。

    实测（ac2 / SEED AC2）：act:34 的 ``a072`` 是**正值幅度**（区间 16–61），
    而 WiFi RSSI 恒为负，所以这里统一取负。已经是负数也照常处理（幂等）。
    """
    raw = to_int_or_none(value)
    if raw is None:
        return None
    return -abs(raw)


# ----------------------------------------------------------------------
# JSON 解析
# ----------------------------------------------------------------------
def _first_json_object(text: str) -> dict[str, Any] | None:
    """从任意文本里截取**第一个完整的** JSON 对象。

    输出里常夹带 lua traceback / 前后噪声，所以不能简单 rfind('}')：
    这里做括号配对，并跳过字符串字面量与转义。
    """
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_str = False
    escaped = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start : idx + 1])
                except json.JSONDecodeError:
                    return None
                return obj if isinstance(obj, dict) else None
    return None


def _res_payload(raw: str) -> str:
    """剥掉 ``req:...`` 前缀，取出 ``res:`` 之后的正文。"""
    for marker in ("\nres:", "\r\nres:"):
        idx = raw.find(marker)
        if idx != -1:
            return raw[idx + len(marker) :]
    stripped = raw.lstrip()
    if stripped.startswith("res:"):
        return stripped[len("res:") :]
    return stripped


def parse_local_cmd_output(raw: str) -> dict[str, Any] | None:
    """解析 ``local_cmd.lua`` 的 ``req:{...}\\nres:{...}`` 输出。

    兼容四种形态：标准 ``res:`` 前缀、无前缀纯 JSON、夹带 lua traceback、
    尾部有日志噪声。兜底手段是从文本里截取**第一个完整 JSON 对象**。
    """
    if not raw:
        return None

    payload = _res_payload(raw).strip()
    if payload:
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict):
            return obj

    return _first_json_object(raw)


def parse_loose_json(raw: str) -> dict[str, Any] | None:
    """容错解析快照文件：原样 / 补外层大括号 / NDJSON 逐行。"""
    text = (raw or "").strip()
    if not text:
        return None

    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass

    # 快照可能被截断成 `"aps":[...]` 这种缺外层大括号的片段
    try:
        obj = json.loads("{" + text.rstrip().rstrip(",") + "}")
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # NDJSON：多个对象换行拼接
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj

    _LOGGER.debug("无法解析 JSON 片段: %s", text[:200])
    return None


def first_dict(value: Any) -> dict[str, Any] | None:
    """``{"rpolicy": [ {...} ]}`` → 首个 dict；也接受直接给 dict。"""
    if isinstance(value, dict):
        return value
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return None


def extract_exsta(exsta_list: Any) -> dict[str, Any] | None:
    """取出 MLO 次链路（``exsta_list``，可能是 list 或 dict）。"""
    if not exsta_list:
        return None
    if isinstance(exsta_list, dict):
        return exsta_list
    if isinstance(exsta_list, list):
        return exsta_list[0] if exsta_list and isinstance(exsta_list[0], dict) else None
    return None


# ----------------------------------------------------------------------
# MAC 工具
# ----------------------------------------------------------------------
_HEX_DIGITS = frozenset("0123456789abcdef")


def normalize_mac(value: str) -> str:
    """归一化为 ``aa:bb:cc:dd:ee:ff``（小写、冒号分隔）。"""
    return (value or "").strip().lower().replace("-", ":")


def is_mac(value: str) -> bool:
    """校验 MAC 格式（支持 ``:`` 或 ``-`` 分隔，大小写不敏感）。"""
    if not value or len(value) != 17:
        return False
    parts = value.replace("-", ":").split(":")
    if len(parts) != 6:
        return False
    return all(len(p) == 2 and all(c in _HEX_DIGITS for c in p.lower()) for p in parts)


def is_infra_mac(value: str) -> bool:
    """是否厂商基础设备（AC / AP 自身）的 MAC。"""
    return normalize_mac(value).startswith(P.INFRA_MAC_PREFIX)


# ----------------------------------------------------------------------
# 展示文本
# ----------------------------------------------------------------------
def is_placeholder(name: str | None) -> bool:
    """主机名是不是「没名字」的占位符。"""
    return (name or "").strip() in P.PLACEHOLDER_HOSTNAMES


def display_hostname(hostname: str | None, mac: str) -> str:
    """有名字就用名字，否则回退 MAC。"""
    return mac if is_placeholder(hostname) else str(hostname).strip()


def format_power_level(code: int | None) -> str:
    """功率档码 → 文案（0 极低 / 1 低 / 2 中 / 3 高）。"""
    if code is None or code < 0:
        return "未知"
    return P.POWER_LEVELS.get(code, f"档 {code}")


def format_port_speed(code: int | None) -> str:
    """端口速率码 → 文案（255 = Auto）。"""
    if code is None or code < 0:
        return "未知"
    if code == P.PORT_SPEED_AUTO:
        return "Auto"
    return P.PORT_SPEED_MAP.get(code, f"{code}")


def format_band(code: int | None) -> str:
    """频段码 → 文案。"""
    if code is None or code < 0:
        return "--"
    return P.BAND_MAP.get(code, "--")


def format_uptime(seconds: int | None) -> str:
    """秒 → ``12天3小时21分``。"""
    if not seconds or seconds < 0:
        return "未知"
    days, rest = divmod(int(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    return f"{days}天{hours}小时{minutes}分"


def to_dbm(raw: Any) -> int | None:
    """漫游策略存储值 → 真实 dBm（存的是 实际值 + 95；0 表示未设置）。"""
    value = to_int_or_none(raw)
    if value is None or value == 0:
        return None
    return value - P.ROAM_RSSI_OFFSET
