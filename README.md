# BeeconMini AC for Home Assistant

![BeeconMini AC](brand/logo.png)


Home Assistant 集成：读取 BeeconMini 无线 AC（SEED AC1/AC2/AC3/AC5）的 AC 主机与纳管 AP 运行状态，并提供终端剔除、AP 重启两个域级服务。

## 支持的设备

- BeeconMini SEED AC1 / AC2 / AC3 / AC5 系列
- 路由器必须运行 iStoreOS 并安装「配网中心」插件（beeconmini2-seed-*）
- 需能通过 LuCI 或 ubus 访问路由器

## 提供的实体

**AC 主设备（1 台）**
- sensor：CPU 温度 / CPU 占用 / 内存占用 / 连接数 / 无线终端数 / 有线终端数 / WAN 接收流量 / WAN 发送流量

**每台纳管 AP（N 台）**
- binary_sensor：在线状态
- sensor：接入终端数（含 MAC / IP / 有线 / 无线 / 已纳管 / 射频数 / 负载）
- sensor：终端清单（含 hostname / IP / MAC / 频段 / 信道 / RSSI / 协议 / 速率 / MLO）

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
