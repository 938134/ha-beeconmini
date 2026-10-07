# BeeconMini AC for Home Assistant

![BeeconMini AC](brand/logo.png)


Home Assistant 集成：读取 BeeconMini 无线 AC（SEED AC1/AC2/AC3/AC5）的 AC 主机与纳管 AP 运行状态，并提供终端剔除、AP 重启两个域级服务。

## 支持的设备

- BeeconMini SEED AC1 / AC2 / AC3 / AC5 系列
- 路由器必须运行 iStoreOS 并安装「配网中心」插件（beeconmini2-seed-*）
- 需能通过 LuCI 或 ubus 访问路由器

## 提供的实体

**AC 主设备（1 台，12 个传感器）**
- sensor：CPU 温度 / CPU 占用 / 内存占用 / 连接数 / 无线终端数 / 有线终端数
- sensor：WAN 接收流量 / WAN 发送流量（累计）
- sensor：AP 总数 / AP 在线数
- sensor：漫游策略（综合 text，attributes 含 2.4G/5G 漫游阈值、剔除阈值、负载均衡、单射频上限）

**每台纳管 AP（N 台，7 个实体）**
- binary_sensor：在线状态
- binary_sensor：端口插线（仅当 AP 详情可用时显示）
- sensor：接入终端数 / 2.4G 终端数 / 5G 终端数
- sensor：端口速率（协商 + 能力 + 插线，仅当详情可用时显示）
- sensor：运行时长（天/时/分，仅当详情可用时显示）
- button：重启 AP

**每台无线终端（M 台，2 个实体）**
- sensor：信号强度（RSSI，dBm）
- button：剔除终端（RSSI / 信道 / Tx-Rx 速率 / 频段 / 协议 / MLO 挂 attributes）

设备层级：AC 主机 → AP（via AC）→ 终端（via AP），终端离线自动清理。

> 注：AP 详情类传感器（端口插线 / 端口速率 / 运行时长 / 2.4G-5G 终端数）依赖 act:31 接口返回。
> 若固件未返回相关字段，这些传感器显示为"不可用"而非错误值。

## 提供的服务

```
beeconmini.kick_client
  mac: "aa:bb:cc:dd:ee:ff"

beeconmini.reboot_aps
  hour: 0-23        # 不填 = 立即重启
  minute: 0-59
  day: 1-8         # 1-7 周一至周日，8 每天（仅定时）
  enabled: true    # 仅定时
```

## 安装（HACS）

1. 打开 HACS → Apps → Download Repositories
2. 粘贴仓库地址 → 下载
3. 重启 Home Assistant
4. Settings → Devices & Services → Add Integration → 搜索 "BeeconMini AC"
5. 输入路由器地址、用户名、密码即可

## 依赖

- aiohttp（Home Assistant 已内建）
- 路由器上已开启 LuCI 或 ubus 访问

## 许可

MIT — 见 [LICENSE](LICENSE)
