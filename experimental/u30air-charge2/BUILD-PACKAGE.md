# charge2 构建与发布包包装配

本说明针对当前文件夹。Linux 内核、OpenWrt 运行时和打包器都必须从其 source/ 子目录取用；分支根目录的同名脚本属于较早的 5.4/6.18 流程。

## 构建 U30 Air 7.2.9 内核及模块

在 Linux 或 WSL 中，进入本目录，准备 Docker。源码中的 Dockerfile 基于 Ubuntu arm64 构建工具。先构建镜像并建立 Linux 源码卷：

```sh
docker build --platform linux/arm64 -t mu300-mainline-build source/upstream
docker volume create mu300-mainline
```

再从内核源卷构建 Image 和匹配模块：

```sh
docker run --platform linux/arm64 --rm -e KV=7.2.9 -e OUTDIR=out-7.2 \
  -v mu300-mainline:/src -v "$PWD/source/upstream":/work \
  mu300-mainline-build bash /work/build.sh
docker run --platform linux/arm64 --rm -e KV=7.2.9 -e OUTDIR=out-7.2 \
  -v mu300-mainline:/src -v "$PWD/source/upstream":/work \
  mu300-mainline-build bash /work/build-modules.sh
```

构建入口会校验内核配置、应用该版本所需的 mainline 移植和补丁，然后只在 7.2.x 分支安装 source/upstream/u30air。保留配置中的 LOCALVERSION=-u30air-charge2。Linux 7.2.9 官方源码归档 SHA256 为 b4c5dfbe51a364a6c7f03869200f88c8e1f77403539005f14b7fc6bc91b8d8ba。生成物位于 source/upstream/out-7.2；该构建不改动分支旧内核流程。

制作可由设备更新器读取的 kernel bundle 时，需要一个已发布的基础 mu300-kernel.tar.gz 提供通用 ramdisk 工具 busybox/logdw：

```sh
MU300_UPSTREAM_OUT="$PWD/source/upstream/out-7.2" \
MU300_KERNEL_DEVICES=u30air \
source/upstream/make-bundle.sh /tmp/mu300-kernel-7.2.tar.gz /path/to/mu300-kernel.tar.gz
```

包内 kernel.release 应为 7.2.9-u30air-charge2，devices 应仅列 u30air。不要将生成物提交进源码仓库。

## 装配完整安装包

完整包装配器不会下载文件或访问设备。需要提供以下上游资产到本地 official-inputs 文件夹：mu300-openwrt-luci-rootfs.tar.gz、mu300-kernel.tar.gz 和 mu300-update。脚本会按自身 PINS 中的 SHA256 校验它们。还需提供与 charge2 构建相符的 U30 Air kernel bundle；正式发布后可从该版本 Release 下载，或从上一步重新构建。

使用新的、尚不存在的输出目录：

```sh
python3 build-full-release.py --repo source --official /path/to/official-inputs \
  --kernel /path/to/mu300-kernel-7.2.tar.gz --docs . --out /path/to/new-output
```

装配器将 source/openwrt/ 下的 LuCI 电源页面、RPC adapter、菜单和 ACL，以及 source/openwrt/overlay/、source/rootfs/ 中的 mu300-power 服务映射到 OpenWrt rootfs；同时将匹配内核模块放到该内核的 modules 目录，并保留源码快照和发布说明。它不会重新编译内核，也不会生成设备专属 boot 镜像或提取目标设备私有固件；这些由完整包安装器在目标设备安装时按既有流程处理。

## 测试与离线校验

针对充电、SOC/FCC、Host 和恢复策略运行源码测试：

```sh
python3 source/upstream/u30air/run-tests.py
```

完整包生成后，可用下列命令核验内核包与 rootfs 是否相符。Python 环境需安装 lz4：

```sh
python3 verify-firmware.py source /path/to/mu300-kernel-7.2.tar.gz \
  /path/to/mu300-openwrt-luci-rootfs.tar.gz
```

包的安装/刷写与源码构建是不同验证阶段。已有硬件证据和未完成验证请看 README.md 与 RELEASE-NOTES.md；离线测试通过不代表完整 ZIP 已经 wipe 安装或完成长期稳定性测试。
