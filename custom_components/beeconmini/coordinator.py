"""BeeconMini AC 数据协调器。

取数逻辑（并发、回退、组装）全部下沉到 :mod:`api.client`，协调器只负责
把它接进 HA 的 DataUpdateCoordinator 生命周期：翻译异常、触发实体更新。

用 asyncio.gather 并发拉取所有独立请求，一次刷新耗时 ≈ 单请求耗时的 1-2 倍，
而不是串行累加。任何子请求失败都不会阻塞主流程，只记录 debug 日志。
"""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import (
    ACState,
    BeeconMiniApiError,
    BeeconMiniAuthError,
    BeeconMiniClient,
    BeeconMiniConnectionError,
)
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

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
        """拉一轮全量状态；核心字段失败即整轮失败，边缘字段降级为空。"""
        try:
            state = await self.client.async_fetch_state()
        except BeeconMiniAuthError as err:
            raise UpdateFailed(f"认证失败：{err}") from err
        except (BeeconMiniConnectionError, BeeconMiniApiError) as err:
            raise UpdateFailed(f"获取 AC 状态失败：{err}") from err

        if state.errors:
            _LOGGER.debug("本轮有降级取数：%s", state.errors)
        return state

    async def async_shutdown(self) -> None:
        await self.client.close()
        await super().async_shutdown()
