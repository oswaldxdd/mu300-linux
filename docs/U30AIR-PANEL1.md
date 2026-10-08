# U30 Air native14 panel1 完整测试包

版本：u30air-native14-panel1-2026.10.08-test；内核：7.2.8-u30air-native14。

将“控制面板”会话的 LuCI 状态、蜂窝锁定、短信、AT、设备与语言页面整合到已校验的 restorefix3 完整 rootfs。增加短信服务、nr6/nr7 独立 AT 通道和手机拨号共享无线操作锁。USB 角色自动发现，角色切换仍由原生 mu300-usb 电源保护执行；默认保留原有 USB 自动策略。

这是完整固件安装包：包括 rootfs、匹配内核和模块、Windows 安装工具、事务回滚脚本、项目对应源码及 Linux 7.2.8 对应源码。无需另装旧版 arch:all APK。内核、充电驱动、FCC 保存恢复、蜂窝/Wi-Fi 网络配置和网卡热插拔恢复逻辑保持基线字节一致。本次仅重新组装 rootfs，未重新编译内核。

## Windows 安装

在解压包目录用 PowerShell 执行（不能在设备 ash 内执行）：

```powershell
.\install.ps1 -Check
.\install.ps1 -Preflight -KnownHostsFile .\device_known_hosts
.\install.ps1 -KnownHostsFile .\device_known_hosts
```

device_known_hosts 必须包含通过可信控制台确认的当前设备主机密钥；本包不附带私人密钥、密码或设备专用 known_hosts。先阅读 Preflight 输出，确认设备和回滚可用。安装脚本保留现有事务备份及回滚，不自动重启；按安装输出完成重启并核对版本。回滚使用包内 rollback.ps1 与安装输出的最新可用备份路径。

当前基线启动镜像不读取 /etc/mu300/usb-net，因此 NCM/ECM/RNDIS 切换控件明确禁用并提示不可用；Host/Device 角色及 USB 网卡桥接功能保留，避免“保存后无效”。完整包不承诺这项尚未实现的启动功能。

## 验证范围

归档和模拟安装测试属于离线验证。本次面板包尚未刷入；网页、短信、锁网、USB 切换和充电的真实设备验证待安装后进行。Android 100% 与 OpenWrt 2% 的电量差异仍未解决；本包不把电量强制改为 100%。包内旧 restorefix3 审计报告和测试记录属于基线历史，不代表新包实机验收。

## 对应源码与重建

corresponding-project-source.tar.gz 保留原有 native14 驱动/安装器源码，在 panel-integration/source 下追加本次面板、拨号锁修复、AT 通道修复和组装/独立检验脚本。corresponding-linux-7.2.8-source.tar.gz 为相同内核的完整对应源码。

```sh
python3 tools/assemble-u30air-panel1.py --base /path/to/restorefix3-release --repo /path/to/source --output /path/to/new/panel1-release
python3 tools/verify-u30air-panel1.py --base /path/to/restorefix3-release --output /path/to/new/panel1-release
```

组装器拒绝覆盖已有输出，校验基线及嵌套 SHA-256，记录完整 rootfs 变更清单；独立检验器检查允许变更范围、源码对应、内核/安装器字节一致以及 ZIP 内容。
