"""BeeconMini 设备交互的异常层级（L0）。

调用方一律捕获这三个子类；``BeeconMiniError`` 只作为统一兜底：

    BeeconMiniError                 基类
    ├── BeeconMiniAuthError         认证失败（账号密码错 / 会话失效）
    ├── BeeconMiniConnectionError   连不上（网络 / 超时 / TLS）
    └── BeeconMiniApiError          设备返回业务错误 / 响应非法 / 入参非法
"""
from __future__ import annotations


class BeeconMiniError(Exception):
    """BeeconMini 交互异常的基类。"""


class BeeconMiniAuthError(BeeconMiniError):
    """认证失败（账号密码错误 / 会话失效）。"""


class BeeconMiniConnectionError(BeeconMiniError):
    """无法连接到路由器。"""


class BeeconMiniApiError(BeeconMiniError):
    """路由器返回了业务错误，或响应无法解析。"""
