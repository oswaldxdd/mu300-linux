# U30 Air charge2 源码与发布入口

本目录对应 **v2026.10.15-charge2**，仅支持 ZTE U30 Air。它把已验证的完整分发源码及发布素材纳入 u30air 分支，同时保留分支现有的 experimental/u30air-native14 和根目录构建流程。

## 哪些源码控制 charge2

分支根目录现在默认构建 charge2；根目录 README.md 和 BUILD-CHARGE2.md 是当前构建入口。此处 source/ 保留为 v2026.10.15-charge2 发布时的完整源码快照。其 upstream/build.sh 在 Linux 7.2.x 路径安装 upstream/u30air 驱动与配置；upstream/build-modules.sh 构建匹配模块；upstream/make-bundle.sh 生成 U30 Air 内核包。该版本使用 Linux 7.2.9-u30air-charge2。

充电上限及其前端和运行时均来自这份快照：内核策略位于 source/upstream/u30air/fcc-learning/source/charge-limit-policy.h 及同目录驱动中；Type-C/PD 实现在 source/upstream/u30air/typec/；WiFi 永久 MAC 修复位于 source/upstream/modules/sprd_wlan_combo/common/iface.c。OpenWrt 电源界面、RPC、持久设置和守护进程位于 source/openwrt/、source/rootfs/，由同目录 source/build-full-release.py 的 rootfs 映射装配。

根目录 upstream/build.sh、upstream/build-modules.sh 已默认选择 7.2.9，根目录 openwrt/、rootfs/ 和 upstream/u30air/ 已同步此版本源码。kernel/build-all.sh 等通用兼容入口仍须显式选择；不要把它们误认为 charge2 默认构建入口。不要把 charge2 的开关单独复制到旧 LuCI；该内核能力和配套 RPC/守护进程必须一起部署。完整固件包仍按本目录的独立安装器和发布说明使用。

## 功能与验证边界

- LuCI 支持 0–100% 整数上限。0% 暂停电池充电，100% 按原厂策略；1–99% 到达上限后暂停，下降 5 个百分点恢复，恢复阈值最低为 0%。
- 上限设置持久保存；容量学习使用独立、有限时长的 quiet lease。原厂 JEITA/温度和充电保护保留。
- Type-C/PD 输入识别、外部供电下手动 USB Host 与 RTL8153、SOC 一位小数显示、受约束的 FCC 容量学习及 WiFi 永久 MAC 修复均属于该 charge2 源码路径。
- 新安装默认上限 100%。设备已验证 0% 暂停、100% 恢复、80% 上限以及通过 RJ45 保持联网；自然放电至恢复阈值、全量 FCC 周期、全范围 SOC 精度和长期稳定性尚未验证。
- charge2 内核包已在设备启动；本目录所附发布记录指出，新组装完整包尚未重新执行 wipe 安装。

发布日志见 RELEASE-NOTES.md，构建和资产校验步骤见 BUILD-PACKAGE.md。本目录不含编译固件、设备日志、分区备份、设备提取的私有固件、密码或个人配置。
