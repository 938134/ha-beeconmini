"""BeeconMini AC 数据协调器。

用 asyncio.gather 并发拉取所有独立请求，一次刷新耗时 ≈ 单请求耗时的 1-2 倍，
而不是串行累加。任何子请求失败都不会阻塞主流程，只记录 debug 日志。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import (
    BeeconMiniApiError,
    BeeconMiniAuthError,
    BeeconMiniClient,
    BeeconMiniConnectionError,
)
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, JSON_SNAPSHOT_FILES
from .model import ACState, build_state

_LOGGER = logging.getLogger(__name__)


class BeeconMiniCoordinator(DataUpdateCoordinator[ACState]):
    """轮询路由器，聚合 AC 全量状态。"""

    def __init__(
        self,
        hass: HomeAssistant,
        client: BeeconMiniClient,
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client

    async def _async_update_data(self) -> ACState:
        """并发拉取所有数据源；核心字段失败即整轮失败，边缘字段降级为空。"""
        # 四个必须成功的基础查询
        product, status, users, wan_stats = await asyncio.gather(
            self.client.async_get_product_info(),
            self.client.async_get_status(),
            self.client.async_get_online_users(),
            self.client.async_get_wan_stats(),
            return_exceptions=True,
        )
        first = product if not isinstance(product, BaseException) else (
            status if not isinstance(status, BaseException) else users
        )
        # 只要有一个真正抛了业务异常（不是超时/连接）就当作 UpdateFailed
        for result, action in (
            (product, "产品信息"),
            (status, "运行状态"),
            (users, "在线终端"),
            (wan_stats, "WAN 流量"),
        ):
            if isinstance(result, BeeconMiniAuthError):
                raise UpdateFailed(f"认证失败：{result}") from result
            if isinstance(result, (BeeconMiniConnectionError, BeeconMiniApiError)):
                raise UpdateFailed(f"获取{action}失败：{result}") from result
        product = product or {}
        status = status or {}
        users = users or []
        wan_stats = wan_stats or {}

        # 边缘数据：stas（无线终端明细）+ AP 快照
        stas, aps_snapshot = await asyncio.gather(
            self.client.async_get_stas(),
            self.client.async_get_json_snapshot(JSON_SNAPSHOT_FILES["aps"]),
            return_exceptions=True,
        )
        if isinstance(stas, BaseException):
            _LOGGER.debug("读取无线终端明细失败: %s", stas)
            stas = []
        if isinstance(aps_snapshot, BaseException):
            _LOGGER.debug("读取 AP 快照失败: %s", aps_snapshot)
            aps_snapshot = None

        state = build_state(
            product=product,
            status=status,
            users_raw=users,
            apinfos_raw=aps_snapshot,
            wan_stats=wan_stats,
            stas_raw=stas,
        )
        _LOGGER.debug(
            "AC 状态刷新：%d 台 AP / %d 台终端",
            len(state.aps),
            len(state.users),
        )
        return state

    async def async_shutdown(self) -> None:
        await self.client.close()
        await super().async_shutdown()
