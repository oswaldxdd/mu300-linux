## 版本更新

- 修复 U30 Air 在 WPA2-PSK/WPA3-SAE 混合模式下无法连接或反复提示密码错误的问题，更新 SC2355 无线驱动、认证程序和无线配置生成逻辑。
- 保留正常四次握手、PMF 和失败校验，并修复切回 WPA2 时残留 WPA3 PWE 配置可能导致的误拒绝。
- 延续 charge2 的充电、Type-C/USB Host 和 LuCI 功能，提供一个完整固件 ZIP。
- 无线驱动、AP 配置生成器和 hostapd 服务配置的发布哈希与真机测试版本一致；r3 的 wpad 增加了 SAE 专用 PWE 检查，修正版通过回归测试，但此精确二进制尚未在设备上单独验证。完整 r3 安装包尚未整体刷入验证。

## 刷入步骤

1. 下载 `U30-Air-Openwrt-r3.zip` 并完整解压。
2. 先备份设备数据和现有配置；U30 Air 需处于已 root 的 Android 系统，开启 USB 调试并连接电脑。
3. 在解压目录打开 PowerShell，先校验：
   ```powershell
   pwsh.exe -NoProfile -File .\install-u30air-host.ps1 -VerifyOnly
   ```
4. 校验通过后执行：
   ```powershell
   pwsh.exe -NoProfile -File .\install-u30air-host.ps1
   ```
5. 按提示确认 `INSTALL`，完成安装并重启。仅用于 U30 Air 专用离线安装流程，请勿作为通用 OpenWrt sysupgrade 镜像刷入；安装及回退限制见包内说明。
