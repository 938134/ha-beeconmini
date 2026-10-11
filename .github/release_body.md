### 📡 BeeconMini 无线 AC

Home Assistant 集成：读取 BeeconMini 无线 AC（SEED AC1/AC2/AC3/AC5）的运行状态，支持终端剔除与 AP 重启。

**核心功能**
- 🖥️ AC 主机 8 项传感器：CPU 温度 / CPU 占用 / 内存 / 连接数 / 无线终端数 / 有线终端数 / WAN 收/发流量
- 📶 每台纳管 AP 3 项实体：在线状态、接入终端数、终端清单（含 RSSI / 信道 / 协议 / 速率 / MLO）
- ⚡ 并发刷新：`asyncio.gather` 一次刷新耗时 ≈ 单请求
- 🎯 两个域级服务：踢终端 `beeconmini.kick_client`、AP 重启 `beeconmini.reboot_aps`（立即/定时）
- 🔌 兼容 SEED AC1 / AC2 / AC3 / AC5（iStoreOS + beeconmini2-seed-* 插件）

**安装方式**
1. 下载 `beeconmini.zip`
2. 解压到 `custom_components/` 目录
3. 重启 Home Assistant
4. Settings → Devices & Services → Add Integration → 搜索 "BeeconMini"

**版本**
- 版本: {{VERSION}}
- 构建日期: {{BUILD_DATE}}
