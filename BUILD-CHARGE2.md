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

## 正式完整固件包

`tools/build-charge2-release.py` 是当前唯一正式 r2 完整包入口。它只接受干净、已提交的最终源码及固定 SHA 输入。内核 bundle 复用之前审核通过的 7.2.9 构建；构建器会比对原构建证明中的每个 `upstream/` 文件和最终提交，验证配置、工具链、构建日志、379 个模块和 vermagic。只要任何内核输入不同，构建就会停止；它不会把旧内核说成从新提交重编译。

Linux/WSL 需 Python 3.12+、GNU `tar`/`gzip`/`patch`、`qemu-aarch64-static`，以及官方 OpenWrt rootfs、回滚 kernel bundle、签名 `packages.adb`、`ucode-mod-socket` APK、审核后的 candidate kernel 和其原始 build evidence：

```sh
python3 tools/build-charge2-release.py \
  --repo . --source-commit <final-40-character-commit> \
  --official-rootfs /path/to/mu300-openwrt-luci-rootfs.tar.gz \
  --rollback-kernel /path/to/mu300-kernel.tar.gz \
  --kernel /path/to/audited-mu300-kernel-7.2.tar.gz \
  --apk-index /path/to/packages.adb \
  --apk /path/to/ucode-mod-socket-2026.01.16~85922056-r2.apk \
  --kernel-provenance /path/to/original-kernel-evidence/BUILD-PROVENANCE.json \
  --kernel-evidence /path/to/original-kernel-evidence \
  --out /path/to/new/v2026.10.15-charge2-r2
```

正式 image 会注册本项目的 `mu300-native-kmod-compat` provider，用 APK solver 校验四项精确版本化内核能力，并创建 FCC `S20` 和 traffic `K10` 链接。provider 只含说明/能力清单和设备端 fail-closed guard，不包含 `.ko`。完整包带 clean source snapshot、kernel reuse provenance、输入资产、manifest、SHA 清单和离线安装包装器；具体 SHA 以构建产物为准。正式包构建成功不代表该精确 ZIP 已刷入设备。

根目录 `build-full-release.py` 是历史固定资产入口，会拒绝当前源码，防止把新运行时与旧内核静默拼包。它不能构建 r2。`tools/build-charge2-test.py` 是独立测试候选入口，继续使用 test ID，不作为正式发布方式。

`experimental/u30air-charge2/source/` 保留旧发布的冻结快照，原包和发布说明不在本次改写范围内。

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
