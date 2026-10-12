# U30 Air charge2 默认构建

`u30air` 分支根目录现在是 charge2 的开发和构建入口。基线为已发布的 `v2026.10.15-charge2`；用户确认该版本稳定运行。本次源码提升不改变充电、SOC/FCC 或 USB 策略，也不引入上游后续功能。

## 默认内核与模块

在 Linux/WSL 中从仓库根目录执行（需要 Docker 的 arm64 支持）：

```sh
docker build --platform linux/arm64 -t mu300-charge2-build upstream
docker volume create mu300-charge2-kernel
docker run --platform linux/arm64 --rm \
  -v mu300-charge2-kernel:/src -v "$PWD/upstream":/work \
  mu300-charge2-build bash /work/build.sh
docker run --platform linux/arm64 --rm \
  -v mu300-charge2-kernel:/src -v "$PWD/upstream":/work \
  mu300-charge2-build bash /work/build-modules.sh
```

两个脚本的默认版本均为 `7.2.9`，默认输出目录为 `upstream/out-7.2`。U30 Air 配置保留 `LOCALVERSION=-u30air-charge2`、原生 SGM41511、FCC/SOC、Type-C/PD、关闭 OTG boost 的外部供电 Host 策略，以及 WiFi 永久 MAC 修复。7.2 内核构建会安装 `upstream/u30air/`；通用的 `kernel/build-all.sh` 不是本版本入口。

生成包含匹配模块和 ramdisk 的内核包，需要已发布的基础 `mu300-kernel.tar.gz` 提供 busybox/logdw：

```sh
sh upstream/make-bundle.sh /tmp/mu300-kernel-charge2.tar.gz /path/to/base/mu300-kernel.tar.gz
```

默认从 `upstream/out-7.2` 打包，`devices` 默认仅为 `u30air`。显式 `KV`、`OUTDIR`、`MU300_UPSTREAM_OUT` 仍可用于兼容构建，但不得把它们当作经过验证的 charge2 固件。

## OpenWrt 与完整发布包

根目录 `openwrt/`、`rootfs/`、`boot/`、`tools/` 已同步发布源码。构建全新 OpenWrt 镜像时用根目录 `openwrt/build-rootfs.sh`，按 `docs/BUILD.md` 准备其模块和构建依赖。

如需用原发布的固定输入资产重新装配完整包，根目录入口会先导出当前已提交的源码，再调用保留的固定资产装配器（Python 3.12 或更高版本）：

```sh
python3 build-full-release.py --official /path/to/official-inputs \
  --kernel /path/to/published-charge2-kernel.tar.gz --out /path/to/new-output
```

此入口拒绝未提交的源码修改；不联网、不访问设备、不编译内核。输入资产和 kernel bundle 必须符合原装配器 SHA256 固定值，新输出目录必须不存在。独立重编译的 kernel bundle 若哈希不同，会被拒绝；发布新二进制需要单独更新版本、资产固定值和验证记录。完整包内的 `install-u30air-host.ps1` 才是原版离线安装入口；根目录通用 `install.ps1` 会选择通用发行版，不应直接用于安装 charge2。

`experimental/u30air-charge2/source/` 是发布时的冻结源码，不再是日常默认开发入口。其发布说明、包内清单和既有下载资产保留不变。

原发布运行时的 `mu300-update` 默认仍指向通用上游发行版，本次为保持 charge2 行为没有修改它。更新 charge2 请使用本 fork 的对应发布包；通用上游在线更新不能视为 charge2 的更新入口。

## 验证

```sh
python3 upstream/u30air/run-tests.py
python3 -m unittest discover -s tests
```

CI 在 `u30air` 推送时运行回归测试和 charge2 策略测试；mainline CI 默认锁定 7.2.9，而不是从 kernel.org 自动选择新版本。源码测试不等同于新编译固件的设备验收；本次未重建或刷写固件。原发布的完整 FCC 周期、全区间 SOC 精度等验证边界见保留的发布说明。
