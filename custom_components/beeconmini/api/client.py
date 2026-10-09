"""业务门面（L4）：把「设备交互」收成一个对象。

对上（HA 集成 / 脚本）只暴露本类：

* 只读方法 ``async_get_*`` —— 一个方法对应一个数据集，内部按
  :mod:`api.protocol` 声明的来源链依次尝试（实时为主、快照兜底）；
* 写方法 ``async_deauth_client`` / ``async_set_reboot_schedule`` /
  ``async_reboot_all_aps`` / ``async_reboot_ap`` / ``async_run_ap_command``；
* 聚合方法 :meth:`BeeconMiniClient.async_fetch_state` —— 并发拉全部数据源，
  组装成一个 :class:`ACState`，并把降级原因记进 ``state.errors``；
* 逃生舱 ``async_raw_*`` —— 需要调新接口时不必改本模块。

异常统一来自 :mod:`api.errors`，不泄露 aiohttp 细节。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from . import protocol as P
from .errors import (
    BeeconMiniApiError,
    BeeconMiniAuthError,
    BeeconMiniConnectionError,
    BeeconMiniError,
)
from .models import ACState, build_state
from .parsers import is_mac, normalize_mac
from .transport import AiohttpTransport

_LOGGER = logging.getLogger(__name__)

# 写入后回读的间隔（秒）。ubus stdout 丢失是概率性的，稍等再读一次能显著提高确认率。
_READBACK_RETRY_DELAY = 1.0

# cocmd CLI 被固件拒绝时会打印的内部标记（MAC 没吃够 18 字符 / 格式错）
_COCMD_REJECT_MARKERS = ("bad mac byte", "fatal error rtb", "entry not found")


def _ap_cocmd_broken(out: str) -> bool:
    """CLI 输出里是否出现固件解析器的拒绝标记（= 参数拼错，命令没发出去）。

    ⚠️ 只认这几个**明确的参数错误**标记。**不要**把「输出为空」或
    「退出码非 0」当成失败 —— 目标 AP 正在重启时本来就不会回包，
    那属于正常的成功路径（实测：重启成功那次 rc=1 且 stdout 为空）。
    """
    low = (out or "").lower()
    return any(m in low for m in _COCMD_REJECT_MARKERS)


class BeeconMiniClient:
    """BeeconMini AC 路由器客户端。"""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        verify_ssl: bool = False,
        *,
        timeout: float = P.TIMEOUT_SECONDS,
        transport: AiohttpTransport | None = None,
    ) -> None:
        self._t = transport or AiohttpTransport(
            host,
            username,
            password,
            verify_ssl=verify_ssl,
            timeout=timeout,
        )
        # 已就「降级」告警过的数据集（避免每轮刷新都刷屏）
        self._degraded_warned: set[str] = set()

    # ------------------------------------------------------------------
    # 基础设施
    # ------------------------------------------------------------------
    @property
    def transport(self) -> AiohttpTransport:
        """底层传输（逃生舱 / 测试注入用）。"""
        return self._t

    @property
    def base_url(self) -> str:
        return self._t.host

    async def close(self) -> None:
        await self._t.close()

    # ------------------------------------------------------------------
    # 来源链解析
    # ------------------------------------------------------------------
    async def _fetch_source(self, source: str) -> Any:
        """按 ``kind:ref`` 取一个来源（``action:<name>`` / ``csdp:<act>``）。"""
        kind, _, ref = source.partition(":")
        if kind == "action":
            return await self._t.action(ref)
        if kind == "csdp":
            data = await self.async_raw_act(int(ref))
            if data.get("errno") not in (0, None):
                raise BeeconMiniApiError(f"act:{ref} 返回 errno={data.get('errno')}")
            return data
        raise BeeconMiniApiError(f"未知来源类型: {source}")

    async def _fetch_first(self, chain: tuple[str, ...]) -> Any:
        """按优先级依次尝试来源链，返回第一个可用结果。"""
        last: Exception | None = None
        for position, source in enumerate(chain):
            try:
                value = await self._fetch_source(source)
            except BeeconMiniError as err:
                last = err
                _LOGGER.debug("%s 取数失败: %s", source, err)
                continue
            if value:
                if position > 0:
                    _LOGGER.debug("已退到兜底来源 %s（%s 不可用）", source, chain[0])
                return value
            last = BeeconMiniApiError(f"{source} 返回空数据")
        raise last or BeeconMiniApiError(f"无可用来源: {' / '.join(chain)}")

    # ------------------------------------------------------------------
    # 逃生舱
    # ------------------------------------------------------------------
    async def async_raw_act(self, act: int, payload: dict[str, Any] | None = None) -> dict:
        """直接发一个 CSDP act（自动带该 act 的固定参数，紧凑 JSON）。"""
        body = {"act": act, **P.ACT_PAYLOAD_EXTRA.get(act, {})}
        if payload:
            body.update(payload)
        return await self._t.csdp(body)

    async def async_raw_action(self, action: str) -> dict:
        """直接走 action 通道。"""
        return await self._t.action(action)

    async def async_raw_shell(self, shell_cmd: str) -> str:
        """在路由器上跑任意 shell。"""
        return await self._t.exec_shell(shell_cmd)

    # ------------------------------------------------------------------
    # 只读：action 通道
    # ------------------------------------------------------------------
    async def async_get_status(self) -> dict[str, Any]:
        """综合状态（LAN / WAN / 流量控制）。"""
        env = await self._fetch_first(P.SOURCE_AC_STATUS)
        return env.get("data", {}) or {}

    async def async_get_product_info(self) -> dict[str, Any]:
        """设备产品信息（型号、温度、内存、CPU、连接数、序列号）。"""
        env = await self._fetch_first(P.SOURCE_AC_PRODUCT)
        return (env.get("data", {}) or {}).get("productinfo", {}) or {}

    async def async_get_online_users(self) -> list[dict[str, Any]]:
        """在线终端列表（DHCP/ARP 视角，含有线 + 无线，**含重复 MAC**）。"""
        env = await self._fetch_first(P.SOURCE_USERS)
        users = (env.get("data", {}) or {}).get("users", [])
        return users if isinstance(users, list) else []

    async def async_get_wan_stats(self) -> dict[str, Any]:
        """WAN 收发字节统计。"""
        env = await self._fetch_first(P.SOURCE_WAN_STATS)
        return env.get("data", {}) or {}

    async def async_run_shell(self, shell_cmd: str) -> str:
        """在路由器上执行一段 shell，返回 stdout+stderr。"""
        return await self._t.exec_shell(shell_cmd)

    # ------------------------------------------------------------------
    # 只读：CSDP / 快照
    # ------------------------------------------------------------------
    async def async_get_stas(self) -> list[dict[str, Any]]:
        """无线终端明细（act:34）。

        **唯一**能拿到「终端归属哪台 AP」的数据源。
        """
        data = await self._fetch_first(P.SOURCE_STA_LIST)
        stas = data.get("stas")
        return stas if isinstance(stas, list) else []

    async def async_get_ap_details(self) -> list[dict[str, Any]]:
        """act:31 AP 管理/状态页明细（**含 AC 主机自身那一行**）。"""
        data = await self._fetch_first(P.SOURCE_AP_DETAILS)
        aps = data.get("aps")
        if not isinstance(aps, list):
            raise BeeconMiniApiError(f"act:31 响应无 aps 列表: keys={list(data)}")
        return aps

    async def async_get_rpolicys(self) -> dict[str, Any]:
        """漫游策略：实时 act:248。

        取不到时直接抛出，由 :meth:`async_fetch_state` 记录降级原因。
        """
        return await self._fetch_first(P.SOURCE_ROAM_POLICY)

    # ------------------------------------------------------------------
    # 写操作
    # ------------------------------------------------------------------
    async def async_deauth_client(self, mac: str) -> bool:
        """厂商原生「剔除终端」（act:249，一次性断连，终端可重连）。

        与 UCI 黑名单封禁不同，这是配网中心前端「手动剔除」按钮的同款命令。
        """
        normalized = normalize_mac(mac)
        if not is_mac(normalized):
            raise BeeconMiniApiError(f"MAC 格式非法: {mac}")
        data = await self.async_raw_act(P.ACT_KICK, {"mac": normalized})
        if data.get("errno") != 0:
            _LOGGER.warning("剔除终端 %s 失败: %s", normalized, data)
            return False
        return True

    async def async_get_reboot_schedule(self) -> dict[str, Any] | None:
        """读取 AP 定时重启配置（``csdp.sys.rben/rbday/rbhour/rbminute``）。

        ``rbday``：1-7 = 周一至周日，8 = 每天。

        **读不到返回 ``None``**（而不是一个「看着像真值」的默认值）——
        调用方必须能区分：

        * ``None`` = **读取失败**（撞上 ubus stdout 丢失，或键不存在）；
        * 返回 dict 但值不符 = **读到了，确实没写进去**。

        旧实现两者都返回默认 dict，导致「读失败」被误判成「写入未生效」。
        """
        raw = await self.async_run_shell(
            f"uci -q get {P.UCI_CSDP}.sys.{P.RB_KEY_ENABLED}; "
            f"uci -q get {P.UCI_CSDP}.sys.{P.RB_KEY_DAY}; "
            f"uci -q get {P.UCI_CSDP}.sys.{P.RB_KEY_HOUR}; "
            f"uci -q get {P.UCI_CSDP}.sys.{P.RB_KEY_MINUTE}"
        )
        vals = [v.strip().strip("'") for v in raw.splitlines() if v.strip()]
        if len(vals) < 4:
            return None
        try:
            return {
                "enabled": vals[0] == "1",
                "day": int(vals[1]),
                "hour": int(vals[2]),
                "minute": int(vals[3]),
            }
        except ValueError:
            return None

    async def async_set_reboot_schedule(
        self, enabled: bool, day: int, hour: int, minute: int
    ) -> bool:
        """写入 AP 定时重启配置（day: 1-7 周一至周日，8 每天），并**独立回读确认**。

        ⚠️ **绝对不能用 stdout 判定写入成败**（v1.3.4 修复的真 bug）。
        ``ubus file.exec`` 有一个已知缺陷：**stdout 会整段丢失** ——
        命令**实际执行成功**，返回的 stdout 却是空串。典型触发就是
        「``uci set`` ×4 + ``uci commit`` + ``echo '[…]'$(uci -q get …)``」
        这种「写完顺便回显」的组合（拆开任一段都正常、与命令长度无关、
        `rpcd` 也没重启）。

        旧实现正是用 ``"[rben=…]" in out`` 判定，于是**写成功了也返回 False**，
        HA 里表现为「配置明明变了，服务却报『未确认成功』」。

        正确做法是「写入」与「校验」**拆成两条命令**：

        1. 写入：``uci set … ×4; uci commit`` —— **不在同一条命令里回读**；
        2. 校验：单独调 :meth:`async_get_reboot_schedule` 独立回读，
           四个字段与期望**完全一致**才返回 ``True``。

        ⚠️ 另需知道：**这个配置位本身不会被执行**（2026-10-08 实测证伪，见
        skill §7.2「AP 定时重启 —— 死配置」）。本方法只保证「写入是否真的落到
        uci」，**不代表 AP 会真的重启**。
        """
        if not 1 <= day <= 8:
            raise BeeconMiniApiError(f"重启日非法（应为 1-8）: {day}")
        if not 0 <= hour <= 23:
            raise BeeconMiniApiError(f"重启小时非法（应为 0-23）: {hour}")
        if not 0 <= minute <= 59:
            raise BeeconMiniApiError(f"重启分钟非法（应为 0-59）: {minute}")

        code = "1" if enabled else "0"
        cmd = (
            f"uci set {P.UCI_CSDP}.sys.{P.RB_KEY_ENABLED}={code}; "
            f"uci set {P.UCI_CSDP}.sys.{P.RB_KEY_DAY}={day}; "
            f"uci set {P.UCI_CSDP}.sys.{P.RB_KEY_HOUR}={hour}; "
            f"uci set {P.UCI_CSDP}.sys.{P.RB_KEY_MINUTE}={minute}; "
            f"uci commit {P.UCI_CSDP}"
        )
        await self.async_run_shell(cmd)

        expected = {"enabled": enabled, "day": day, "hour": hour, "minute": minute}
        for attempt in (1, 2):
            actual = await self.async_get_reboot_schedule()
            if actual == expected:
                return True
            if attempt == 1:
                # 回读失败（None）与值不符都再给一次机会：stdout 丢失是概率性的。
                _LOGGER.debug("AP 重启计划回读未确认（第 1 次）: %s", actual)
                await asyncio.sleep(_READBACK_RETRY_DELAY)
                continue
            if actual is None:
                _LOGGER.warning(
                    "AP 重启计划写入后**无法回读**（stdout 丢失或 uci 读取失败），"
                    "写入本身可能已成功：期望 %s",
                    expected,
                )
            else:
                _LOGGER.warning(
                    "AP 重启计划写入未生效：期望 %s，实际 %s", expected, actual
                )
            return False
        return False

    async def async_reboot_all_aps(self) -> bool:
        """立即重启**全部纳管 AP**（真实通道：``bxplug -m "csdp:reboot"``）。

        机制（2026-10-08 实机确认，两次触发均有效）：

        * 走 bxplug 守护进程的 CLI 模块：``bxplug -m "csdp:reboot"``
          —— 等价于「按下复位键」，成功时 stdout 回 ``OK``；
        * **AC 自身不重启**，只有纳管 AP 会重启；
        * AP 掉线约 **40–110 秒**后自行回线上线（实测三台全部自动恢复）。

        ⚠️ **不要再走 ``csdp.sys.rben`` 定时重启位。** 那条路是死的：
        ``csdp_reboot.c`` 最终要执行 ``/usr/share/rtmgr/reboot %u``，
        而该脚本**不在插件包里**（``opkg files beeconmini2-seed-ac2`` 的
        228 个文件里没有它），所以定时重启永远不可能生效。

        ⚠️ **不要用 stdout 判定成败**（ubus ``file.exec`` 的 stdout 会整段丢失）：
        这里用「独立探针」的方式确认 —— 先跑 CLI，再用一条单独的 ``echo``
        验证通道确实能把 stdout 带回来，两者都正常才算成功。

        副作用：触发瞬间 bxplug 会打印内部断言
        （``assert len failed`` / ``priv not init!``），属已知现象，会自行恢复。
        """
        out = await self.async_run_shell(P.CMD_REBOOT_APS)
        ack = await self.async_run_shell(P.CMD_REBOOT_ACK)

        if P.REBOOT_ACK_MARKER not in ack:
            # 连 echo 都带不回来 → stdout 通道整体异常，无法判定
            _LOGGER.warning(
                "AP 重启已下发，但 stdout 通道异常（独立 echo 探针无回显），无法确认"
            )
            return False
        if "OK" not in out:
            _LOGGER.warning("AP 重启下发失败：bxplug 返回 %r", out)
            return False
        return True

    # ------------------------------------------------------------------
    # 单台 AP：任意 shell / 单台重启（cocmd 通道）
    # ------------------------------------------------------------------
    async def async_run_ap_command(self, mac: str, command: str) -> str:
        """在**指定的一台 AP** 上执行 shell，返回该 AP 命令的 stdout。

        机制：``bxplug -m "urtm:sm:comsg:sm:cocmd:cmd:<mac18><cmd>"`` ——
        把命令经 AC→AP 的 comsg 通道下发，AP 侧 ``/usr/share/rtmgr/cocmd``
        脚本会 ``eval`` 它，然后把结果经 ``cocmd:res`` 回传，
        **回传内容会直接出现在 CLI 的 stdout 里**。

        :raises BeeconMiniApiError: 命令里出现固件解析器的错误标记
            （``bad mac byte`` / ``fatal error rtb``），说明参数拼错了。

        ⚠️ 这是**以 root 在 AP 上跑任意命令**的通道，由厂商固件原生提供。
        调用方自己保证 ``command`` 安全；集成只做「透传 + 错误识别」。

        ⚠️ 返回的 stdout 内容取决于 AP 是否及时回包：
        * 命令很快返回（``echo`` / ``logread``）→ 能拿到输出；
        * 命令导致 AP 掉线（``reboot``）→ 拿不到输出，且 CLI 退出码为 1，
          属**正常现象**，不代表命令没发出去。
        """
        try:
            cli = P.build_ap_cocmd(mac, command)
        except ValueError as err:
            raise BeeconMiniApiError(str(err)) from err
        out = await self.async_run_shell(cli)
        if _ap_cocmd_broken(out):
            raise BeeconMiniApiError(
                f"cocmd 参数被固件拒绝（{mac}）：{out.strip()[:200]}"
            )
        return out

    async def async_reboot_ap(self, mac: str) -> bool:
        """重启**指定的那一台 AP**（cocmd 通道，其余 AP 不受影响）。

        * 在目标 AP 上 ``eval reboot``；**AC 自身不重启**；
        * 定向由 MAC 决定 —— 实机验证：只有目标 AP 运行时长归零，
          同网另外两台 AP 的运行时长连续增长；
        * AP 掉线→回上线实测约 **90 秒**（Mini3000；不同型号略有差异）。

        ⚠️ **不要用 CLI 退出码判成败**：AP 重启时来不及回 ``cocmd:res``，
        ``bxplug`` 会返回 **1**（实测「重启成功」的那次就是 rc=1）。
        退出码 1 表示「本轮没收到回包」，不是「命令没发出去」。
        真正的确认口径是**观察这台 AP**：先掉线，再回来时 ``a01`` 归零。

        返回 ``True`` 表示「命令已下发且未被固件拒绝」；
        会话/通道层面的异常会向上抛 :class:`BeeconMiniConnectionError`。
        """
        await self.async_run_ap_command(mac, P.AP_REBOOT_COMMAND)
        _LOGGER.info(
            "已向 AP %s 下发 reboot（cocmd 通道）；"
            "它会在数十秒内掉线并自行回上线，届时运行时长归零",
            mac,
        )
        return True

    # ------------------------------------------------------------------
    # 聚合：一轮完整取数
    # ------------------------------------------------------------------
    async def async_fetch_state(self) -> ACState:
        """并发拉全部数据源，组装成 :class:`ACState`。

        * **核心数据集**（产品信息 / 运行状态 / 在线终端 / WAN 流量）
          任一失败即抛出，由调用方把整轮标记为失败；
        * **边缘数据集**（AP 实时明细 / 无线终端 / 漫游策略）
          失败只降级为空，并把原因记进 :attr:`ACState.errors`。

        AP 清单只来自 ``act:31``；它整条挂掉时，仍会由 ``act:34`` 的归属
        信息兜底建出 AP 条目，保证终端的 ``via_device`` 能解析到父设备
        （见 :func:`models._build_aps`）。
        """
        errors: dict[str, str] = {}

        core = await asyncio.gather(
            self.async_get_product_info(),
            self.async_get_status(),
            self.async_get_online_users(),
            self.async_get_wan_stats(),
            return_exceptions=True,
        )
        for result in core:
            if isinstance(result, BaseException):
                raise result
        product, status, users_raw, wan_stats = core

        ap_details, stas_raw, rpolicys_raw = await asyncio.gather(
            self._edge(self.async_get_ap_details, "AP 实时明细", errors),
            self._edge(self.async_get_stas, "无线终端明细", errors),
            self._edge(self.async_get_rpolicys, "漫游策略", errors),
        )

        state = build_state(
            product=product if isinstance(product, dict) else {},
            status=status if isinstance(status, dict) else {},
            users_raw=users_raw if isinstance(users_raw, list) else [],
            wan_stats=wan_stats if isinstance(wan_stats, dict) else {},
            stas_raw=stas_raw if isinstance(stas_raw, list) else [],
            ap_details_raw=ap_details if isinstance(ap_details, list) else [],
            rpolicys_raw=rpolicys_raw if isinstance(rpolicys_raw, dict) else None,
            errors=errors,
        )
        _LOGGER.debug(
            "AC 状态刷新：%d 台 AP / %d 台无线终端 / %d 台有线终端%s",
            len(state.aps),
            len(state.stas),
            state.wired_client_count,
            f"（降级：{errors}）" if errors else "",
        )
        return state

    async def _edge(self, fetcher: Any, label: str, errors: dict[str, str]) -> Any:
        """边缘数据集：失败降级为 None 并记录原因，绝不抛出。

        首次降级时打一条 **WARNING** —— 否则「AP 型号/IP/端口速率一直是未知」
        这类问题在默认日志级别下完全不可见（旧实现只写 DEBUG，排查全靠猜）。
        同一数据集只提示一次，恢复后打 INFO 复位。
        """
        try:
            value = await fetcher()
        except BeeconMiniError as err:
            _LOGGER.debug("%s 取数失败（降级为空）: %s", label, err)
            errors[label] = str(err)
            if label not in self._degraded_warned:
                self._degraded_warned.add(label)
                _LOGGER.warning(
                    "%s 取数失败，相关实体将显示为「未知/0」：%s"
                    "（同一数据集只提示一次，恢复后会再提示）",
                    label,
                    err,
                )
            return None
        if label in self._degraded_warned:
            self._degraded_warned.discard(label)
            _LOGGER.info("%s 取数已恢复正常", label)
        return value


async def fetch_state(
    host: str,
    username: str,
    password: str,
    *,
    verify_ssl: bool = False,
) -> ACState:
    """一次性取一轮状态（用完即关会话）的便捷函数。"""
    client = BeeconMiniClient(host, username, password, verify_ssl=verify_ssl)
    try:
        return await client.async_fetch_state()
    finally:
        await client.close()


__all__ = [
    "BeeconMiniClient",
    "BeeconMiniApiError",
    "BeeconMiniAuthError",
    "BeeconMiniConnectionError",
    "BeeconMiniError",
    "fetch_state",
]
