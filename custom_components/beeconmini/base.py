"""BeeconMini AC 实体基类：AC 主机 / AP / 终端三层设备。"""
from __future__ import annotations

from collections.abc import Callable

from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import BeeconMiniCoordinator


class ACEntityBase(CoordinatorEntity[BeeconMiniCoordinator]):
    """挂在 AC 主设备下的实体基类。"""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BeeconMiniCoordinator) -> None:
        super().__init__(coordinator)
        dev = coordinator.data.device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.config_entry.entry_id)},
            name=f"AC · {dev.model or 'SEED'}",
            manufacturer=MANUFACTURER,
            model=dev.model or "SEED AC",
            sw_version=dev.version or None,
            serial_number=dev.sn or None,
            configuration_url=coordinator.client.base_url,
            connections={("mac", dev.mac)} if dev.mac else set(),
        )


class APEntityBase(CoordinatorEntity[BeeconMiniCoordinator]):
    """挂在单台 AP 设备下的实体基类。"""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BeeconMiniCoordinator, ap_mac: str) -> None:
        super().__init__(coordinator)
        self._ap_mac = ap_mac

    @property
    def _ap(self):
        return self.coordinator.data.ap_by_mac(self._ap_mac)

    @property
    def available(self) -> bool:
        return super().available and self._ap is not None

    @property
    def device_info(self) -> DeviceInfo:
        ap = self._ap
        name = ap.display_name if ap else self._ap_mac
        model = (ap.ap_model or "无线 AP") if ap else "无线 AP"
        sw_version = ap.ap_version or None if ap else None
        hw_version = ap.ap_sn or None if ap else None
        return DeviceInfo(
            identifiers={(DOMAIN, self._ap_mac)},
            name=f"AP · {name}",
            manufacturer=MANUFACTURER,
            model=model,
            sw_version=sw_version,
            hw_version=hw_version,
            serial_number=ap.ap_sn or None,
            # ⚠️ 只用 MAC 作设备标识：IP 会变（DHCP），且与其他集成撞车时
            #    HA 会把两个设备误判为同一个。
            connections={("mac", self._ap_mac)},
            via_device=(DOMAIN, self.coordinator.config_entry.entry_id),
        )

class ClientEntityBase(CoordinatorEntity[BeeconMiniCoordinator]):
    """挂在单台无线终端设备下的实体基类。

    一台无线终端 = 一个 device，**``via_device`` 指向它当前接入的 AP**。
    这样 HA 会在 AP 设备页原生渲染出「已连接的设备」卡片列出这些终端，
    点进去即是该终端的详情页。

    终端设备页与 AP 设备页同构（都由 HA 原生渲染，不需要自研 UI）：

    ==================  =========================================
    AP 设备页            终端设备页
    ==================  =========================================
    设备信息：型号 / 固件   设备信息：型号「无线终端」/ 序列号
    版本 / 序列号 / MAC    / **MAC**（connections）/ 已连接到 AP
    控制：重启 AP         控制：剔除终端
    传感器：信道 / 功率…   传感器：信号强度 / 频段 / 信道 / 协议 /
                       发送速率 / 接收速率 / IP / MLO
    ==================  =========================================

    **MAC 不做传感器**：交给设备信息的 ``connections``（HA 会渲染成
    「MAC: xx:xx:…」，装了 DHCP 集成时还可点击跳转），并同时写入
    ``serial_number``（终端没有可读的硬件序列号，MAC 就是它的唯一标识）。

    ⚠️ `sta.ap_mac` 取自 act:34 的 ``s111``，实测与 act:31 的 ``a00``
    **完全相等**（2026-10-08 实机复核三台全中），所以父设备能正确解析。
    就算 act:31 整条挂掉，``models._build_aps`` 也会用 act:34 的
    ``s111`` / ``s112`` 兜底建出 AP 条目，链接不会断；只有 act:31 与 act:34
    **双双**失败时，``via_device`` 才会退化为 None（终端仍会出现，
    只是暂时不挂在 AP 之下）。
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: BeeconMiniCoordinator, sta_mac: str) -> None:
        super().__init__(coordinator)
        self._sta_mac = sta_mac

    @property
    def _sta(self):
        return self.coordinator.data.sta_by_mac(self._sta_mac)

    @property
    def available(self) -> bool:
        return super().available and self._sta is not None

    @property
    def device_info(self) -> DeviceInfo:
        sta = self._sta
        ap_via: tuple[str, str] | None = None
        if sta and sta.ap_mac:
            ap_via = (DOMAIN, sta.ap_mac)
        name = sta.display_name if sta else self._sta_mac
        return DeviceInfo(
            identifiers={(DOMAIN, self._sta_mac)},
            # 与「AP · 悦房」同构的命名，便于在 AP 页的「已连接的设备」里辨认
            name=f"STA · {name}",
            manufacturer=MANUFACTURER,
            model="无线终端",
            # MAC 只放设备信息：不单独做传感器
            serial_number=self._sta_mac,
            connections={("mac", self._sta_mac)},
            via_device=ap_via,
        )


# ----------------------------------------------------------------------
# 动态实体注册器（AP / 终端上线自动注册，离线自动移除）
# ----------------------------------------------------------------------
class DynamicEntityTracker:
    """统一管理动态实体（AP / 终端）的注册与移除。

    每个平台（sensor / binary_sensor / button）各自持有一个 Tracker 实例，
    通过 factory 回调创建实体。协调器每次刷新后调用 ``on_update()`` 同步。

    **终端迁移**：终端在 AP 间漫游时，实体（unique_id 不变）的
    ``via_device`` 由 ``ClientEntityBase.device_info`` 动态更新，
    Tracker 不需要介入——它只管实体的**增删**，不管 ``via_device`` 变化。
    """

    def __init__(
        self,
        *,
        entity_type: str,
        factory: Callable[[str], list[Entity]],
        offline_grace: int = 3,
    ) -> None:
        """
        :param entity_type: 'ap' 或 'sta'，决定从 coordinator.data 取哪个列表。
        :param factory: 收到一个 mac，返回要注册的实体列表。
        :param offline_grace: 实体离线多少轮后才移除（避免网络抖动误删）。
        """
        self._type = entity_type
        self._factory = factory
        self._offline_grace = offline_grace
        self._known: dict[str, int] = {}  # mac → 连续离线次数
        self._added: set[str] = set()     # 已注册的 mac

    def on_update(
        self,
        coordinator: BeeconMiniCoordinator,
        async_add_entities: Callable,
    ) -> None:
        """协调器刷新后调用，同步实体增删。"""
        entities = coordinator.data.aps if self._type == "ap" else coordinator.data.stas
        macs = {getattr(e, "mac", "") for e in entities if getattr(e, "mac", "")}
        macs.discard("")

        # 1. 新出现的 → 注册
        new_macs = macs - self._added
        if new_macs:
            new_entities: list[Entity] = []
            for mac in new_macs:
                self._added.add(mac)
                self._known.pop(mac, None)
                new_entities.extend(self._factory(mac))
            if new_entities:
                async_add_entities(new_entities)

        # 2. 消失的 → 记录离线次数
        disappeared = self._added - macs
        for mac in disappeared:
            self._known[mac] = self._known.get(mac, 0) + 1

        # 3. 连续离线超过宽限期 → 标记为可移除
        #    （实际移除需要平台层配合 async_remove_entities 或 entity_registry）
        #    这里只清理内部记录，HA 的 entity_registry 会保留离线实体。
        for mac in list(self._known):
            if self._known[mac] >= self._offline_grace:
                self._added.discard(mac)
                self._known.pop(mac, None)

        # 4. 仍然在线的 → 清零离线计数
        for mac in macs:
            self._known.pop(mac, None)

    @property
    def known_macs(self) -> set[str]:
        """当前已注册且在线的 MAC 集合。"""
        return set(self._added)


__all__ = [
    "ACEntityBase",
    "APEntityBase",
    "ClientEntityBase",
    "DynamicEntityTracker",
]
