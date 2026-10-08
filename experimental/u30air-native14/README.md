# U30 Air native14 / restorefix3 — 实验代码

最新设备测试版本：`u30air-native14-restorefix3-2026.10.08-test`，内核 `7.2.8-u30air-native14`。

这里保存已经生成并安装的完整测试固件对应的驱动、运行时服务、安装/回滚代码和离线测试。主线默认安装和构建流程仍独立使用原有配置；合并本目录不会自动安装实验固件。

## 本次提交包含什么

- `source/`：原固件组件源码，包括 USB Host、SGM41511、燃料计和策略。
- `fcc-learning/source/`：实际 native14 内核采用的 FCC 学习、静默采样、满电校正和充电器协调代码；这是 native14 电池改动的权威版本。`source/` 中早期同名组件保留为构建历史输入。
- `overlay/` 和 `fcc-learning/runtime/`：RTL8153B LAN 桥接/热插拔、学习容量记录及恢复服务。
- `install-device.sh` / `rollback-device.sh`：完整固件事务安装和恢复。`mu300-update` 修复“boot 已是最新版”仍返回 1 的问题；对应 overlay 使用相同修复。
- 观察脚本、策略/故障注入/更新器测试，以及 `SOURCE-PROVENANCE.json` 的来源和逐文件校验值。

本仓库不存储设备分区镜像、厂商私有文件、个人配置、SSH 密钥、原始设备日志或编译出的固件。已有 `v2026.10.04-u30air-native2-hotplugfix` release 是旧版本，不是此 native14 版本。

## 验证状态与已知问题（2026-10-08）

restorefix3 完整安装返回 0，重启后通过固定主机密钥的 SSH 确认新版本、内核、更新器及 3,983 mAh 学习记录恢复。早期 native14 曾取得持续 120 秒的 100% / Full / 低净电流端点和 3,983 mAh 学习记录；这些结果不等于 restorefix3 的完整硬件验收。

**尚未解决：OpenWrt 显示 2%，几乎没有继续充电即切换到 Android，Android 显示 100%。** 当前代码可能采用过时但合法的保存电量作为初始基准；Android 参考驱动还读取另一份常温电量。没有原始寄存器证据前，不认定根因，也不强制显示 100%。独立百分比准确度、此版本的受保护满电终止及换接网卡后的持续采样仍未完成。备份清理必须先恢复管理连接并重新核验最近一份完整恢复集。

## 离线测试

Linux / WSL 需要 Python 3、GCC 和 POSIX shell：

```sh
python3 experimental/u30air-native14/run-tests.py
```

运行源码校验、三个 C 策略/状态夹具、五个实际代码故障/生命周期测试和语法检查。所有生成文件位于临时目录。CI 执行同一命令。

若另有对应内核包，完整更新器/安装器测试使用普通临时文件，不接触设备分区：

```sh
python3 experimental/u30air-native14/run-tests.py \
  --kernel-bundle /path/to/mu300-kernel-7.2.8-u30air-native14.tar.gz
```

内核包必须匹配来源清单中的 SHA-256。不要用“离线通过”宣称实机电量准确。

## 构建和安装边界

`fcc-learning/build-complete-kernel.py` 等脚本保留本次构建实际使用的隔离目录和历史输入约定；不是任意新克隆上的一键构建入口。重建前需要对应 Linux 7.2.8 源码、匹配配置、模块、工具链及脚本引用的前代输入，按 `SOURCE-PROVENANCE.json` 核验来源。已有源码包 SHA-256 为 `2719af88a15c28780d96f040e10fcfffbf7b096db1a2b199b9cff5bf03572bc7`；已安装 ZIP SHA-256 为 `2d3feb8c90ddd33e90f864c85b6f9b8fab2363f745c0736e969974aada8c03dd`。

`docs/BUILD-HISTORY.md` 和 `README-restorefix*.md` 是冻结候选构建当时的记录，其中“尚未刷入”等历史措辞不代表当前状态。本文是本目录当前状态说明。`install-kernel-device.sh` 是早期 native11→native12 工具，**禁止用于 native14**。没有匹配且完整的已校验发布包时，不要直接运行设备安装脚本。

保留上游许可证和各源码文件的原有 SPDX/署名。Android 参考源码的 commit 位于 `android-reference/COMMIT`，不将其等同于设备中未经核验的厂商二进制。
