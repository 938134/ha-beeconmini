"""传输层（L1）：ubus / LuCI / CSDP 两条通道 + 会话与登录。

与路由器通信有两条通道，各自有明确分工：

1. **action 通道**（ubus JSON-RPC 优先，LuCI web_action 兜底）
   ``POST /ubus`` → ``session.login`` 取 token → ``file.exec`` 执行
   ``lua /usr/share/rtmgr/local_cmd.lua '{"action":"..."}'``；
   失败则回落 ``POST /cgi-bin/luci/admin/beeconmini2/web_action?auth=<sysauth>``。
   两条路径返回**同构**信封 ``{"data": {...}, "error": 0, "reason": "ok"}``。

2. **CSDP 直通通道**（实时）
   ``POST /cgi-bin/luci/admin/beeconmini2/api`` → ``/tmp/bxplug.sock``。
   ⚠️ 必须发**紧凑 JSON**：``{"act": 31}``（带空格）返回 ``{"errno":2}``。

> v1.3.3 起移除了「读 ``/tmp/json/*`` 快照文件」的第三条通道 ——
> 那批文件是静态遗留（数月不更新），把过期配置值当兜底只会误导。
> 详见 :mod:`protocol` 模块头。
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
from typing import Any

import aiohttp

from . import protocol as P
from .errors import (
    BeeconMiniApiError,
    BeeconMiniAuthError,
    BeeconMiniConnectionError,
)
from .parsers import parse_local_cmd_output

_LOGGER = logging.getLogger(__name__)


class AiohttpTransport:
    """基于 aiohttp 的传输实现（HA 集成使用；测试可注入假 session 或子类打桩）。"""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        verify_ssl: bool = False,
        timeout: float = P.TIMEOUT_SECONDS,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._host = (host or "").strip().rstrip("/")
        self._username = username
        self._password = password
        self._verify_ssl = verify_ssl
        self._timeout = timeout
        self._session = session
        self._owns_session = session is None

        # SSL context 只建一次，避免每个连接都 create_default_context()
        self._ssl_ctx: ssl.SSLContext | None = None
        if not verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            self._ssl_ctx = ctx

        self._ubus_token: str | None = None
        self._sysauth: str | None = None
        # 登录去重锁：并发取数时避免多个协程同时登录、互相覆盖凭证
        self._ubus_lock = asyncio.Lock()
        self._luci_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 基础设施
    # ------------------------------------------------------------------
    @property
    def host(self) -> str:
        """规范化后的基地址（自动补 https://）。"""
        if self._host.startswith(("http://", "https://")):
            return self._host
        return f"https://{self._host}"

    @property
    def base_url(self) -> str:
        """兼容旧调用方（历史版本里叫 ``base_url``）。"""
        return self.host

    def _make_connector(self) -> aiohttp.TCPConnector:
        if self._verify_ssl:
            return aiohttp.TCPConnector()
        return aiohttp.TCPConnector(ssl=self._ssl_ctx)

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # ⚠️ unsafe=True 是必须的（v1.3.2 修复）：
            # aiohttp 的 CookieJar 默认（unsafe=False）会**静默丢弃 IP 地址
            # 主机下发的 cookie** —— 用户在 HA 里填 192.168.9.1 这类 IP 时，
            # LuCI 登录的 302 + Set-Cookie 会被 jar 直接扔掉，
            # 表现为「HTTP 302 但拿不到 sysauth」→ 整条 CSDP 通道失效。
            # 本 session 只服务这一台路由器，放开无安全副作用。
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._timeout),
                connector=self._make_connector(),
                cookie_jar=aiohttp.CookieJar(unsafe=True),
                headers={"Content-Type": "application/json"},
            )
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        """关闭自建的 session（外部注入的 session 不动）。"""
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()
        if self._owns_session:
            self._session = None

    async def _post_json(
        self,
        path: str,
        payload: dict[str, Any],
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """POST 一个 JSON 信封，把 HTTP/网络错误翻译成我们的异常。"""
        session = await self._ensure_session()
        url = f"{self.host}{path}"
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
                    raise BeeconMiniApiError(f"响应不是合法 JSON: {text[:200]}") from err
        except aiohttp.ClientError as err:
            raise BeeconMiniConnectionError(f"连接失败: {err}") from err
        except asyncio.TimeoutError as err:
            raise BeeconMiniConnectionError("请求超时") from err

    # ------------------------------------------------------------------
    # ubus 通道
    # ------------------------------------------------------------------
    async def ubus_login(self) -> str:
        """``session.login`` 取 ubus token。"""
        data = await self._post_json(
            P.PATH_UBUS,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "call",
                "params": [
                    P.UBUS_NULL_TOKEN,
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
            raise BeeconMiniAuthError(f"ubus 登录失败 (code={result[0]})，请检查账号密码")
        token = result[1].get("ubus_rpc_session")
        if not token:
            raise BeeconMiniApiError("ubus 登录未返回会话 token")
        self._ubus_token = token
        return token

    async def _get_ubus_token(self) -> str:
        """拿（可能已缓存的）token，并发安全。"""
        async with self._ubus_lock:
            if self._ubus_token:
                return self._ubus_token
            return await self.ubus_login()

    async def ubus_exec(self, shell_cmd: str, token: str) -> str:
        """``file.exec`` 执行一段 shell，返回 stdout。"""
        data = await self._post_json(
            P.PATH_UBUS,
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
            self._ubus_token = None
            raise BeeconMiniAuthError("ubus 会话已失效")
        if result[0] != 0:
            raise BeeconMiniApiError(f"ubus exec 失败 (code={result[0]})")
        return result[1].get("stdout", "")

    async def exec_shell(self, shell_cmd: str, *, merge_stderr: bool = True) -> str:
        """在路由器上执行 shell；会话失效自动重登一次。

        ``merge_stderr=True`` 时把 stderr 一起收进 stdout（调试方便，
        也是历史行为）；读快照文件时传 False，避免文件不存在时污染输出。
        """
        cmd = f"{shell_cmd} 2>&1" if merge_stderr else shell_cmd
        token = await self._get_ubus_token()
        try:
            return await self.ubus_exec(cmd, token)
        except BeeconMiniAuthError:
            self._ubus_token = None
            token = await self.ubus_login()
            return await self.ubus_exec(cmd, token)

    # ------------------------------------------------------------------
    # LuCI 通道
    # ------------------------------------------------------------------
    async def luci_login(self) -> str:
        """表单登录 LuCI，取 ``sysauth`` cookie。

        ⚠️ 实测坑（HA 长跑必踩，v1.2.1 修复）
            LuCI 在**已持有会话 cookie** 时再次登录，会直接渲染首页并返回
            ``HTTP 200`` —— **不再返回 302，也不再下发新 cookie**。

            旧实现按「必须 302/303」判定，于是在会话过期后重登时必然失败，
            而重登失败会让**整条 CSDP 通道（act:31 / act:34 / act:248）永久失效**，
            表现为「AP 型号/IP/运行时长/端口速率 一直显示未知、终端数为 0」。

            两个修正：

            1. 登录前先清空 cookie jar（本 session 只服务这一台路由器，
               清空无副作用），保证每次都能拿到 302 + 新 cookie；
            2. 成功判据改为「cookie jar 里是否出现 sysauth」，**不看状态码**。

            v1.3.2 再补两道保险（实机新坑）：

            3. 自建 session 换 ``CookieJar(unsafe=True)`` —— aiohttp 默认
               **静默丢弃 IP 地址主机下发的 cookie**，用户填 192.168.9.1
               这类 IP 时 302 + Set-Cookie 全部白搭；
            4. jar 里没有时，直接解析 ``Set-Cookie`` 响应头兜底。
        """
        session = await self._ensure_session()
        session.cookie_jar.clear()

        url = f"{self.host}{P.PATH_LUCI_LOGIN}"
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
                new_cookie: str | None = None
                for cookie in session.cookie_jar:
                    if "sysauth" in cookie.key:
                        new_cookie = str(cookie.value)
                        break
                if not new_cookie:
                    # 兜底：不从 jar 里拿，直接解析 Set-Cookie 响应头。
                    # 覆盖「jar 因主机类型/策略丢 cookie」的极端场景。
                    getall = getattr(resp.headers, "getall", None)
                    raw_headers = (
                        getall("Set-Cookie", [])
                        if callable(getall)
                        else ([resp.headers.get("Set-Cookie")]
                              if resp.headers.get("Set-Cookie") else [])
                    )
                    for raw in raw_headers:
                        for part in str(raw).split(";"):
                            part = part.strip()
                            if part.lower().startswith("sysauth") and "=" in part:
                                new_cookie = part.split("=", 1)[1].strip()
                                break
                        if new_cookie:
                            break
                if new_cookie:
                    self._sysauth = new_cookie
                    return new_cookie
                if resp.status in (401, 403):
                    raise BeeconMiniAuthError(
                        f"LuCI 登录被拒 (HTTP {resp.status})，请检查账号密码"
                    )
                raise BeeconMiniAuthError(
                    "LuCI 登录未拿到 sysauth cookie"
                    f" (HTTP {resp.status}, Location={resp.headers.get('Location')!r})"
                )
        except aiohttp.ClientError as err:
            raise BeeconMiniConnectionError(f"LuCI 登录连接失败: {err}") from err
        except (asyncio.TimeoutError, TimeoutError) as err:
            # aiohttp 的超时不是 ClientError 子类，不接住会冒泡出异常体系
            raise BeeconMiniConnectionError(f"LuCI 登录超时: {err}") from err

    async def _get_sysauth(self) -> str:
        async with self._luci_lock:
            if self._sysauth:
                return self._sysauth
            return await self.luci_login()

    async def web_action(self, action: str) -> dict[str, Any]:
        """调用厂商 Web UI 同款端点；会话失效自动重登一次。"""
        last_err: Exception | None = None
        for _ in range(2):
            sysauth = await self._get_sysauth()
            try:
                data = await self._post_json(
                    P.PATH_WEB_ACTION, {"action": action}, params={"auth": sysauth}
                )
            except BeeconMiniAuthError as err:
                async with self._luci_lock:
                    self._sysauth = None
                last_err = err
                continue
            if data.get("error") != 0:
                raise BeeconMiniApiError(
                    f"web_action {action} 返回错误: {data.get('reason')}"
                )
            return data
        raise last_err or BeeconMiniApiError(f"web_action {action} 失败")

    async def action(self, name: str) -> dict[str, Any]:
        """统一查询入口：ubus ``local_cmd`` 优先，失败回落 LuCI ``web_action``。"""
        try:
            cmd = f'lua {P.LOCAL_CMD_SCRIPT} \'{{"action":"{name}"}}\''
            raw = await self.exec_shell(cmd)
            parsed = parse_local_cmd_output(raw)
            if parsed is not None:
                return parsed
            _LOGGER.debug("action %s：ubus 输出无法解析，回落 web_action", name)
        except BeeconMiniAuthError:
            _LOGGER.debug("action %s：ubus 会话失效，回落 web_action", name)
        except (BeeconMiniApiError, BeeconMiniConnectionError) as err:
            _LOGGER.debug("action %s：ubus 通道失败（%s），回落 web_action", name, err)
        return await self.web_action(name)

    # ------------------------------------------------------------------
    # CSDP 直通通道
    # ------------------------------------------------------------------
    async def csdp(
        self, payload: dict[str, Any], *, path: str = P.PATH_CSDP
    ) -> dict[str, Any]:
        """向 CSDP 端点发送**紧凑 JSON**。

        实测坑：端点背后是嵌入式守护进程（bxplug / rtlgsw），其 JSON 解析器
        不接受空格——``{"act": 240}`` 返回 ``errno:2``，``{"act":240}`` 才正常。
        Cookie 认证失败（返回 HTML 登录页）时自动重登一次。
        """
        body = json.dumps(payload, separators=(",", ":")).encode()
        session = await self._ensure_session()
        last_err: Exception | None = None

        for _ in range(2):
            sysauth = await self._get_sysauth()
            url = f"{self.host}{path}"
            try:
                async with session.post(
                    url,
                    data=body,
                    params={"auth": sysauth},
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    text = await resp.text()
                    stripped = text.lstrip()

                    # 会话失效时 LuCI 返回登录页 HTML(200) 或 403
                    if resp.status in (401, 403) or not stripped.startswith("{"):
                        async with self._luci_lock:
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
                        raise BeeconMiniApiError(f"CSDP 响应非法: {text[:200]}") from err
            except aiohttp.ClientError as err:
                async with self._luci_lock:
                    self._sysauth = None
                last_err = BeeconMiniConnectionError(f"CSDP 连接失败: {err}")
            except (asyncio.TimeoutError, TimeoutError) as err:
                # aiohttp 的超时不是 ClientError 子类，必须单独接住，
                # 否则会冒泡出 BeeconMiniError 体系、拖垮整轮刷新。
                last_err = BeeconMiniConnectionError(f"CSDP 请求超时: {err}")

        raise last_err or BeeconMiniApiError("CSDP 请求失败")

    # ------------------------------------------------------------------
    # 快照通道（v1.3.3 已移除）
    # ------------------------------------------------------------------
    # 历史上这里有 ``snapshot_text`` / ``snapshot_json``：通过
    # ``file.exec`` 读路由器上的 ``/tmp/json/*``。实测那批文件是**静态遗留**
    # （apinfos 只在配置变更时写、baseinfo/devinfo 停在 8 个月前），
    # 信道值 6 个里 4 个与实际不符 —— 当兜底只会把过期值显示成实时值。
    # 现已整体删除，AP 清单只认 act:31（见 protocol / models._build_aps）。
