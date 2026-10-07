"""BeeconMini 无线 AC 配置流。"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .api import (
    BeeconMiniAuthError,
    BeeconMiniClient,
    BeeconMiniConnectionError,
)
from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USERNAME,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_USERNAME, default=DEFAULT_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): bool,
    }
)


class BeeconMiniConfigFlow(ConfigFlow, domain=DOMAIN):
    """处理集成配置。"""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            # 去重：同主机只允许一个实例
            await self.async_set_unique_id(host.lower())
            self._abort_if_unique_id_configured()

            client = BeeconMiniClient(
                host=host,
                username=user_input.get(CONF_USERNAME, DEFAULT_USERNAME),
                password=user_input[CONF_PASSWORD],
                verify_ssl=user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            )
            try:
                info = await client.async_get_product_info()
            except BeeconMiniAuthError:
                errors["base"] = "invalid_auth"
            except BeeconMiniConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 - 兜底，避免配置流崩溃
                _LOGGER.exception("配置 BeeconMini AC 时发生未知错误")
                errors["base"] = "unknown"
            else:
                model = info.get("model") or "BeeconMini AC"
                return self.async_create_entry(
                    title=f"{model} ({host})",
                    data=user_input,
                )
            finally:
                await client.close()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return BeeconMiniOptionsFlow()


class BeeconMiniOptionsFlow(OptionsFlow):
    """可选设置：轮询间隔 / 证书校验。"""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=current.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(int, vol.Range(min=10, max=600)),
                vol.Optional(
                    CONF_VERIFY_SSL,
                    default=current.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
