"""BeeconMini 设备交互层（``api`` 包）。

集成里**所有**与路由器打交道的代码都在这个包内，其余模块只通过
``from .api import ...`` 使用它。分四层 + 两个常量层，依赖单向向下、无环：

    client.py      L4 门面  BeeconMiniClient：业务方法 / 聚合取数 / 写操作
    models.py      L3 数据  dataclass 与字段解码（唯一知道 a00/s112/r03 含义的地方）
    parsers.py     L2 解析  原始文本 → dict；字段 → 语义值（纯函数）
    transport.py   L1 传输  ubus / LuCI / CSDP 三条通道 + 会话与登录
    protocol.py    L0 协议  端点、act 码、action 字、快照路径、回退链、枚举表
    errors.py      L0 异常  统一异常层级

调用方示例::

    from .api import BeeconMiniClient, BeeconMiniAuthError

    client = BeeconMiniClient(host, user, password, verify_ssl=False)
    try:
        state = await client.async_fetch_state()      # 一轮全量
        print(state.device.model, [ap.name for ap in state.aps])
    finally:
        await client.close()
"""
from __future__ import annotations

from . import models, parsers, protocol, transport
from .client import BeeconMiniClient, fetch_state
from .errors import (
    BeeconMiniApiError,
    BeeconMiniAuthError,
    BeeconMiniConnectionError,
    BeeconMiniError,
)
from .models import (
    ACState,
    BeeconAP,
    BeeconDeviceInfo,
    BeeconSta,
    BeeconUser,
    RPolicy,
    build_state,
)
from .parsers import (
    format_band,
    format_port_speed,
    format_power_level,
    format_uptime,
    is_mac,
    normalize_mac,
)
from .transport import AiohttpTransport

__all__ = [
    # 客户端
    "BeeconMiniClient",
    "fetch_state",
    "AiohttpTransport",
    # 异常
    "BeeconMiniError",
    "BeeconMiniAuthError",
    "BeeconMiniConnectionError",
    "BeeconMiniApiError",
    # 数据模型
    "ACState",
    "BeeconAP",
    "BeeconSta",
    "BeeconUser",
    "BeeconDeviceInfo",
    "RPolicy",
    "build_state",
    # 工具
    "is_mac",
    "normalize_mac",
    "format_power_level",
    "format_port_speed",
    "format_band",
    "format_uptime",
    # 子模块
    "protocol",
    "parsers",
    "models",
    "transport",
]
