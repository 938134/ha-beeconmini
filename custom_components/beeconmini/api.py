"""BeeconMini AC 路由器 API 客户端。

与路由器通信有两条独立通道，互为备份：

1. **ubus JSON-RPC**（推荐，最稳）
   `POST /ubus` → `session.login` 取 token → `file.exec` 执行
   `lua /usr/share/rtmgr/local_cmd.lua '{"action":"..."}'`

2. **LuCI web_action**（厂商 Web UI 同款）
   `POST /cgi-bin/luci/admin/beeconmini2/web_action?auth=<sysauth>`

两条通道都返回同构 JSON：``{"data": {...}, "error": 0, "reason": "ok"}``。
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
from typing import Any

import aiohttp

from .const import (
    ACTION_PRODUCT,
    ACTION_STATUS,
    ACTION_USERS,
    ACTION_WAN_STATS,
    ACT_KICK,
    ACT_STA_GET,
    CSDP_API_PATH,
    CSDP_UCI,
    LOCAL_CMD_SCRIPT,
    LUCI_LOGIN_PATH,
    LUCI_WEB_ACTION,
    RB_DEFAULT_DAY,
    RB_DEFAULT_HOUR,
    RB_DEFAULT_MINUTE,
    UBUS_NULL_TOKEN,
    UBUS_PATH,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=25)


class BeeconMiniAuthError(Exception):
    """认证失败（账号密码错误 / 会话失效）。"""


class BeeconMiniConnectionError(Exception):
    """无法连接到路由器。"""


class BeeconMiniApiError(Exception):
    """路由器返回了业务错误。"""


class BeeconMiniClient:
    """BeeconMini AC 路由器客户端。"""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        verify_ssl: bool = False,
    ) -> None:
        self._host = host.rstrip("/")
        self._username = username
        self._password = password
        self._verify_ssl = verify_ssl

        self._session: aiohttp.ClientSession | None = None
        self._ubus_token: str | None = None
        self._sysauth: str | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 基础设施
    # ------------------------------------------------------------------
    @property
    def base_url(self) -> str:
        """规范化后的基地址（自动补 https://）。"""
        if self._host.startswith(("http://", "https://")):
            return self._host
        return f"https://{self._host}"

    def _make_connector(self) -> aiohttp.TCPConnector:
        if self._verify_ssl:
            return aiohttp.TCPConnector()
        # 家用路由器多为自签证书，默认跳过校验
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return aiohttp.TCPConnector(ssl=ctx)

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=REQUEST_TIMEOUT,
                connector=self._make_connector(),
                headers={"Content-Type": "application/json"},
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _post_json(
        self,
        path: str,
        payload: dict[str, Any],
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        session = await self._ensure_session()
        url = f"{self.base_url}{path}"
        try:
            async with session.post(url, json=payload, params=params) as resp:
                text = await resp.text()
                if resp.status in (401, 403):
                    raise BeeconMiniAuthError(f"认证被拒 (HTTP {resp.status})")
                if resp.status >= 400:
                    raise BeeconMiniApiError(f"HTTP {resp.status}: {text[:200]}")
                try:
                    return json.loads(text)
                except json.JSONDecodeError as err:
                    raise BeeconMiniApiError(
                        f"响应不是合法 JSON: {text[:200]}"
                    ) from err
        except aiohttp.ClientError as err:
            raise BeeconMiniConnectionError(f"连接失败: {err}") from err
        except asyncio.TimeoutError as err:
            raise BeeconMiniConnectionError("请求超时") from err

    # ------------------------------------------------------------------
    # 通道 A：ubus JSON-RPC
    # ------------------------------------------------------------------
    async def _ubus_login(self) -> str:
        data = await self._post_json(
            UBUS_PATH,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "call",
                "params": [
                    UBUS_NULL_TOKEN,
                    "session",
                    "login",
                    {
                        "username": self._username,
                        "password": self._password,
                        "timeout": 3600,
                    },
                ],
            },
        )
        result = data.get("result")
        if not isinstance(result, list) or not result:
            raise BeeconMiniApiError(f"ubus 登录响应异常: {data}")
        if result[0] != 0:
            raise BeeconMiniAuthError(
                f"ubus 登录失败 (code={result[0]})，请检查账号密码"
            )
        token = result[1].get("ubus_rpc_session")
        if not token:
            raise BeeconMiniApiError("ubus 登录未返回会话 token")
        self._ubus_token = token
        return token

    async def _ubus_exec(self, shell_cmd: str, token: str) -> str:
        data = await self._post_json(
            UBUS_PATH,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "call",
                "params": [
                    token,
                    "file",
                    "exec",
                    {"command": "/bin/sh", "params": ["-c", shell_cmd]},
                ],
            },
        )
        result = data.get("result")
        if not isinstance(result, list) or not result:
            raise BeeconMiniApiError(f"ubus exec 响应异常: {data}")
        if result[0] == 6:
            raise BeeconMiniAuthError("ubus 会话已失效")
        if result[0] != 0:
            raise BeeconMiniApiError(f"ubus exec 失败 (code={result[0]})")
        return result[1].get("stdout", "")

    # ------------------------------------------------------------------
    # 通道 B：LuCI web_action（cookie 会话）
    # ------------------------------------------------------------------
    async def _luci_login(self) -> str:
        session = await self._ensure_session()
        url = f"{self.base_url}{LUCI_LOGIN_PATH}"
        form = {
            "luci_username": self._username,
            "luci_password": self._password,
        }
        try:
            async with session.post(
                url,
                data=form,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                allow_redirects=False,
            ) as resp:
                if resp.status not in (302, 303):
                    raise BeeconMiniAuthError(
                        f"LuCI 登录失败 (HTTP {resp.status})，请检查账号密码"
                    )
                for cookie in session.cookie_jar:
                    if "sysauth" in cookie.key:
                        self._sysauth = cookie.value
                        return cookie.value
        except aiohttp.ClientError as err:
            raise BeeconMiniConnectionError(f"LuCI 登录连接失败: {err}") from err
        raise BeeconMiniAuthError("LuCI 登录成功但未拿到 sysauth cookie")

    async def _web_action(self, action: str, sysauth: str) -> dict[str, Any]:
        return await self._post_json(
            LUCI_WEB_ACTION, {"action": action}, params={"auth": sysauth}
        )

    # ------------------------------------------------------------------
    # 统一查询入口
    # ------------------------------------------------------------------
    async def _query(self, action: str) -> dict[str, Any]:
        """查询一个 action，优先 ubus，失败自动回落到 web_action。"""
        async with self._lock:
            try:
                token = self._ubus_token or await self._ubus_login()
                cmd = (
                    f"lua {LOCAL_CMD_SCRIPT} '{{\"action\":\"{action}\"}}' 2>&1"
                )
                raw = await self._ubus_exec(cmd, token)
                parsed = _parse_local_cmd_output(raw)
                if parsed is not None:
                    return parsed
            except BeeconMiniAuthError:
                self._ubus_token = None
            except (BeeconMiniApiError, BeeconMiniConnectionError) as err:
                _LOGGER.debug(
                    "ubus 通道查询 %s 失败，回落 web_action: %s", action, err
                )

            try:
                sysauth = self._sysauth or await self._luci_login()
                data = await self._web_action(action, sysauth)
                if data.get("error") == 0:
                    return data
                raise BeeconMiniApiError(
                    f"web_action {action} 返回错误: {data.get('reason')}"
                )
            except BeeconMiniAuthError:
                self._sysauth = None
                raise

    async def async_get_status(self) -> dict[str, Any]:
        """综合状态（LAN / WAN / 流量控制）。"""
        return (await self._query(ACTION_STATUS)).get("data", {})

    async def async_get_product_info(self) -> dict[str, Any]:
        """设备产品信息（型号、温度、内存、CPU、连接数、序列号）。"""
        return (await self._query(ACTION_PRODUCT)).get("data", {}).get(
            "productinfo", {}
        )

    async def async_get_online_users(self) -> list[dict[str, Any]]:
        """在线终端列表（含 DHCP/ARP 视角的有线 + 无线）。"""
        return (await self._query(ACTION_USERS)).get("data", {}).get("users", [])

    async def async_get_wan_stats(self) -> dict[str, Any]:
        """WAN 收发字节统计。"""
        return (await self._query(ACTION_WAN_STATS)).get("data", {})

    async def async_get_json_snapshot(self, path: str) -> dict[str, Any] | None:
        """读取路由器上的 JSON 快照文件（AP 清单等）。"""
        try:
            token = self._ubus_token or await self._ubus_login()
            raw = await self._ubus_exec(f"cat {path} 2>/dev/null", token)
        except (BeeconMiniAuthError, BeeconMiniApiError, BeeconMiniConnectionError):
            return None
        if not raw.strip():
            return None
        return _parse_loose_json(raw)

    # ------------------------------------------------------------------
    # CSDP 直通通道：POST 紧凑 JSON 到 /api
    # ------------------------------------------------------------------
    async def _post_csdp(self, endpoint_path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """向 CSDP 直通端点发送**紧凑 JSON**（关键：separators 去空格）。

        实测坑：这些端点背后是嵌入式守护进程（bxplug / rtlgsw），
        其 JSON 解析器不接受空格——``{"act": 240}`` 返回 errno:2，
        ``{"act":240}`` 才正常。Cookie 认证失败（返回 HTML）自动重登一次。
        """
        body = json.dumps(payload, separators=(",", ":"))
        session = await self._ensure_session()
        last_err: Exception | None = None
        for _ in range(2):
            sysauth = self._sysauth or await self._luci_login()
            url = f"{self.base_url}{endpoint_path}"
            try:
                async with session.post(
                    url,
                    data=body.encode(),
                    params={"auth": sysauth},
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    text = await resp.text()
                    stripped = text.lstrip()
                    # 认证失效时 LuCI 返回登录页 HTML（200）或 403
                    if resp.status in (401, 403) or not stripped.startswith("{"):
                        self._sysauth = None
                        last_err = BeeconMiniAuthError(
                            f"CSDP 会话失效 (HTTP {resp.status})"
                        )
                        continue
                    if resp.status >= 400:
                        raise BeeconMiniApiError(f"HTTP {resp.status}: {text[:200]}")
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError as err:
                        raise BeeconMiniApiError(
                            f"CSDP 响应非法: {text[:200]}"
                        ) from err
            except aiohttp.ClientError as err:
                self._sysauth = None
                last_err = BeeconMiniConnectionError(f"CSDP 连接失败: {err}")
        raise last_err or BeeconMiniApiError("CSDP 请求失败")

    async def async_get_stas(self) -> list[dict[str, Any]]:
        """无线终端明细（act:34，走 bxplug）。

        这是**唯一**能拿到「终端归属哪台 AP」的数据源。
        """
        data = await self._post_csdp(CSDP_API_PATH, {"act": ACT_STA_GET, "c1150": 0})
        if data.get("errno") not in (0, None):
            _LOGGER.warning("读取无线终端明细失败: %s", data)
            return []
        stas = data.get("stas")
        return stas if isinstance(stas, list) else []

    async def async_deauth_client(self, mac: str) -> bool:
        """厂商原生「剔除终端」（act:249，走 bxplug）。

        与黑名单封禁不同：这是配网中心前端「手动剔除」按钮的同款命令，
        一次性断连，终端可重新接入。
        """
        mac = normalize_mac(mac)
        if not is_mac(mac):
            raise BeeconMiniApiError(f"MAC 格式非法: {mac}")
        data = await self._post_csdp(CSDP_API_PATH, {"act": ACT_KICK, "mac": mac})
        if data.get("errno") != 0:
            _LOGGER.warning("剔除终端 %s 失败: %s", mac, data)
            return False
        return True

    # ------------------------------------------------------------------
    # AP 定时重启（csdp.sys.rben/rbday/rbhour/rbminute）
    # ------------------------------------------------------------------
    async def async_run_shell(self, shell_cmd: str) -> str:
        """在路由器上执行一段 shell，返回 stdout+stderr。"""
        async with self._lock:
            token = self._ubus_token or await self._ubus_login()
            try:
                return await self._ubus_exec(f"{shell_cmd} 2>&1", token)
            except BeeconMiniAuthError:
                self._ubus_token = None
                token = await self._ubus_login()
                return await self._ubus_exec(f"{shell_cmd} 2>&1", token)

    async def async_get_reboot_schedule(self) -> dict[str, Any]:
        """读取 AP 定时重启配置。

        字段来自固件配置模板 csdp.sys（AC 会下发给纳管 AP）：
          rben    : 0/1 开关
          rbday   : 1-7 = 周一至周日，8 = 每天
          rbhour  : 0-23
          rbminute: 0-59
        """
        raw = await self.async_run_shell(
            f"uci -q get {CSDP_UCI}.sys.rben; "
            f"uci -q get {CSDP_UCI}.sys.rbday; "
            f"uci -q get {CSDP_UCI}.sys.rbhour; "
            f"uci -q get {CSDP_UCI}.sys.rbminute"
        )
        vals = [v.strip().strip("'") for v in raw.splitlines() if v.strip()]
        if len(vals) < 4:
            return {
                "enabled": False,
                "day": RB_DEFAULT_DAY,
                "hour": RB_DEFAULT_HOUR,
                "minute": RB_DEFAULT_MINUTE,
            }
        try:
            return {
                "enabled": vals[0] == "1",
                "day": int(vals[1]),
                "hour": int(vals[2]),
                "minute": int(vals[3]),
            }
        except ValueError:
            return {
                "enabled": False,
                "day": RB_DEFAULT_DAY,
                "hour": RB_DEFAULT_HOUR,
                "minute": RB_DEFAULT_MINUTE,
            }

    async def async_set_reboot_schedule(
        self, enabled: bool, day: int, hour: int, minute: int
    ) -> bool:
        """写入 AP 定时重启配置。day: 1-7 周一至周日，8 每天。"""
        if not 1 <= day <= 8:
            raise BeeconMiniApiError(f"重启日非法（应为 1-8）: {day}")
        if not 0 <= hour <= 23:
            raise BeeconMiniApiError(f"重启小时非法（应为 0-23）: {hour}")
        if not 0 <= minute <= 59:
            raise BeeconMiniApiError(f"重启分钟非法（应为 0-59）: {minute}")

        cmd = (
            f"uci set {CSDP_UCI}.sys.rben={'1' if enabled else '0'}; "
            f"uci set {CSDP_UCI}.sys.rbday={day}; "
            f"uci set {CSDP_UCI}.sys.rbhour={hour}; "
            f"uci set {CSDP_UCI}.sys.rbminute={minute}; "
            f"uci commit {CSDP_UCI}; "
            f"echo '[rben='$(uci -q get {CSDP_UCI}.sys.rben)"
            f"' day='$(uci -q get {CSDP_UCI}.sys.rbday)"
            f"' hour='$(uci -q get {CSDP_UCI}.sys.rbhour)"
            f"' min='$(uci -q get {CSDP_UCI}.sys.rbminute)']'"
        )
        out = await self.async_run_shell(cmd)
        expected = f"[rben={'1' if enabled else '0'} day={day} hour={hour} min={minute}]"
        return expected in out

    async def async_get_ap_details(self) -> list[dict[str, Any]]:
        """act:31 AP 管理/状态页：型号、版本、端口速率、运行时长、2.4G/5G 终端拆分。"""
        data = await self._post_csdp(self.CSDP_API_PATH, {"act": 31})
        return data.get("aps", []) or []

    async def async_get_rpolicys(self) -> dict[str, Any] | None:
        """漫游策略快照（/tmp/json/rpolicys）。"""
        return await self.async_get_json_snapshot("/tmp/json/rpolicys")

    async def async_reboot_all_aps(self) -> bool:
        """立即重启全部纳管 AP。

        实现机制（实测确认）：AP 的定时重启由固件配置位
        ``csdp.sys.rben/rbday/rbhour/rbminute`` 控制，AC 在**下一次配置下发**
        时会把该配置推给各 AP。因此"立即重启"的做法是：
        设置一个"当前时间 +1 分钟"的一次性重启点并开启开关，让 AC 下发后触发。

        注意：BeeconMini 固件**没有**独立的即时重启 AP 命令。
        """
        now = await self.async_run_shell("date '+%H %M'")
        parts = now.split()
        if len(parts) < 2:
            raise BeeconMiniApiError(f"无法读取路由器时间: {now!r}")
        try:
            hour = int(parts[0])
            minute = int(parts[1]) + 1
        except ValueError as err:
            raise BeeconMiniApiError(f"路由器时间格式异常: {now!r}") from err
        if minute >= 60:
            minute -= 60
            hour = (hour + 1) % 24
        return await self.async_set_reboot_schedule(True, RB_DEFAULT_DAY, hour, minute)


# ----------------------------------------------------------------------
# MAC 工具
# ----------------------------------------------------------------------
def is_mac(value: str) -> bool:
    """校验 MAC 地址格式（支持 : 或 - 分隔）。"""
    if not value or len(value) != 17:
        return False
    parts = value.replace("-", ":").split(":")
    if len(parts) != 6:
        return False
    return all(
        len(p) == 2 and all(c in "0123456789abcdef" for c in p.lower())
        for p in parts
    )


def normalize_mac(value: str) -> str:
    """归一化 MAC 为 ``aa:bb:cc:dd:ee:ff`` 形式。"""
    return value.strip().lower().replace("-", ":")


# ----------------------------------------------------------------------
# 响应解析
# ----------------------------------------------------------------------
def _parse_local_cmd_output(raw: str) -> dict[str, Any] | None:
    """解析 ``local_cmd.lua`` 的 ``req:...\nres:{...}`` 输出。"""
    if not raw:
        return None
    marker = "\nres:"
    idx = raw.find(marker)
    if idx == -1:
        if raw.lstrip().startswith("{"):
            payload = raw
        else:
            return None
    else:
        payload = raw[idx + len(marker):]
    payload = payload.strip()
    if not payload:
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        # 输出里可能夹带 traceback，尝试截取第一个完整 JSON 对象
        start = payload.find("{")
        end = payload.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(payload[start : end + 1])
            except json.JSONDecodeError:
                return None
        return None


def _parse_loose_json(raw: str) -> dict[str, Any] | None:
    """容错解析形如 ``"aps":[...]``（缺外层大括号）的片段。"""
    text = raw.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 补外层大括号
    try:
        return json.loads("{" + text.rstrip().rstrip(",") + "}")
    except json.JSONDecodeError:
        pass
    # NDJSON：多个对象换行拼接
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    _LOGGER.debug("无法解析 JSON 片段: %s", text[:200])
    return None
