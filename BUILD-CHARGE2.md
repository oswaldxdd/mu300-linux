# U30 Air charge2 默认构建

当前正式发布目标为 `v2026.10.15-charge2-r2`。基线是已发布且用户确认稳定的 `v2026.10.15-charge2`；本次不更改 charge2 充电、SOC/FCC 或 USB 供电策略。正式版对应 OpenWrt 25.12.5 和 Linux `7.2.9-u30air-charge2`，仅面向 ZTE U30 Air。

## 内核

日常内核源码位于 `upstream/`，默认配置保留 U30 Air 原生 SGM41511、FCC/SOC、Type-C/PD、外部供电下手动 Host、关闭 OTG boost，以及 Wi-Fi 永久 MAC 修复。完整内核构建入口为 `upstream/build.sh` 和 `upstream/build-modules.sh`，安装 U30 Air 设备文件与补丁；不能用通用 `kernel/build-all.sh` 代替。

```sh
docker build --platform linux/arm64 -t mu300-charge2-build upstream
docker volume create mu300-charge2-kernel
docker run --platform linux/arm64 --rm -v mu300-charge2-kernel:/src -v "$PWD/upstream":/work \
  mu300-charge2-build bash /work/build.sh
docker run --platform linux/arm64 --rm -v mu300-charge2-kernel:/src -v "$PWD/upstream":/work \
  mu300-charge2-build bash /work/build-modules.sh
```

内核包需要已发布的基础 `mu300-kernel.tar.gz` 提供 busybox/logdw：

```sh
sh upstream/make-bundle.sh /tmp/mu300-kernel-charge2.tar.gz /path/to/base/mu300-kernel.tar.gz
```

## 默认正式完整固件包（r3）

当前默认发布目标为 `v2026.10.15-charge2-r3`。请用 `tools/build-charge2-release-r3.py` 构建完整 ZIP；`tools/build-charge2-release.py` 保留原 r2 的严格基线验证，只能装配与旧 upstream 内核输入字节一致的版本，不能绕过 r3 的 Wi-Fi 模块重建检查。

r3 构建分三步：`tools/build-charge2-sae-offload-wifi.py` 从固定 OpenWrt SDK 与 base feed commit 构建并验证 r6 hostapd APK；`tools/build-charge2-sae-offload-kernel.py` 使用已审核内核编译树新建 `sprd_wlan_combo.ko`，只替换基础 kernel bundle 中该模块；最后 `tools/build-charge2-release-r3.py` 更新 rootfs APK 数据库、核验 AP ucode、装配单一完整 ZIP。内核 `Image`、generic ramdisk 与另外 378 个模块字节复用自审核基础 bundle，构建器不称它们为新编译产物。

具体 SDK/feed、module build、package build 命令与固定输入见 **[R3 完整构建说明](docs/BUILD-CHARGE2-RELEASE-R3.md)**。构建要求 clean committed source，最终 ZIP 的 `source_commit` 必须对应实际公开源码提交。单元测试、SDK/build audit 与 `-VerifyOnly` 都不等于完整固件已在 U30 Air 刷写或启动；manifest 保持对应硬件字段为 false，直到对精确 ZIP 完成设备验证。

旧 r2 与当前 r3 构建边界分别记录在各自文档中。根目录 `build-full-release.py` 和 `tools/make-release.sh` 是历史/通用发布入口，不装配 charge2 r3 固件。`tools/build-charge2-test.py` 仍是测试候选入口，不作为正式发布方式。

## charge2 测试候选

测试候选使用独立的 `tools/build-charge2-test.py`。它要求一个干净已提交源码、测试专用 fresh kernel bundle、匹配当前提交的 kernel provenance，以及固定 OpenWrt 输入；不能通过改名把测试候选伪装成正式版本。

先用 Windows Git 导出干净源码：

```powershell
powershell.exe -ExecutionPolicy Bypass -File tools/export-charge2-test-source.ps1 `
  -Repo . -OutDir C:\mu300-charge2-test-source
```

构建测试候选前，使用 `tools/capture-charge2-kernel-provenance.py` 将编译树的 `upstream/` 输入与同一 source archive 比较，然后将生成的 provenance/evidence 传给测试 builder。完整测试候选装配命令见历史测试 package `BUILD-CHARGE2-TEST.md`；它与正式 r2 builder 分开维护。

## 更新器和验证边界

`mu300-update check` 仅在设备标识为 `u30air` 且运行内核为 `7.2.9-u30air-charge2` 时查询 `oswaldxdd/mu300-linux`；其他设备或内核仍查询通用上游 `dikeckaan/mu300-linux`，显式设置 `MU300_REPO` 可覆盖默认值。该路由只决定版本检查和发布资产来源；charge2 的在线系统、kernel、boot 更新仍被拦截，升级请使用匹配版本的完整离线包。

运行 `python3 -m unittest discover -s tests` 和 `python3 upstream/u30air/run-tests.py`。源代码测试、构建器/包审计和设备运行验证分别记录；不要把前两项说成完整硬件验证。具体 release manifest 说明该精确 r2 ZIP 的设备验证状态。
