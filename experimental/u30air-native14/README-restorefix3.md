# U30 Air native14 restorefix3 候选固件

版本：`u30air-native14-restorefix3-2026.10.08-test`  
内核：`7.2.8-u30air-native14`

restorefix3 从冻结的 restorefix2 完整包生成，保留已编译内核、FCC 充电学习代码、RTL8153B 网络桥接和安装/回滚路径。它修复了更新器在 boot 镜像已与当前 Image、generic ramdisk 和已写入 boot SHA 一致时，仍因可选重启提示的 shell 条件返回 1 而触发安装器回滚的问题。现在相同且已验证的 boot 状态会成功返回 0；真实 boot 更新失败仍返回非零。更新器修复同时进入安装包内的 `mu300-update`、新 OpenWrt rootfs 的 `/opt/mu300/bin/mu300-update`，以及对应源码中的 `project/rootfs` 和 `native14` 两份 overlay。

内核包 SHA-256 保持为 `cc7c5d7bdf9e983f5d8fd87f6aebf9ec0cac68c76d5964218e24c0644b9becf8`，没有重新编译或修改驱动。修复后的更新器 SHA-256 为 `9a6f94fa86caa57dfcfa0594fcb357e36e39158513a8f22bf11174d9aa15b606`。

离线验证覆盖安装器的 40 个场景：39 个临时文件模拟场景，加上一次真实 `mu300-update` 与安装器集成的失败/回滚场景。集成场景用真实 31 个模块和普通文件模拟 boot 分区；更新器先复制模块，再遇到无效 Android boot 头而失败，安装器随后恢复旧 root、移除新加入的模块目录并读回原 boot 文件。独立更新器测试还验证实际 boot 更新、已是最新时不写入并返回 0、缺少 boot 分区时仍返回非零。输出记录和 SHA-256 见发布包内的审查报告及测试日志。

**该候选包尚未刷入设备。** 上述 shell/临时文件检查不代表真实设备上的启动、充电、热插拔、网络或电量验证。之前在不同 native14 镜像上观察到的 100%/Full 端点不能作为本候选包的硬件结果。电池百分比的独立准确度及低电量端行为仍未完成验证。

后续完成低电量端测量时，应使用随发布资料提供的 `observe-native14-discharge.sh`、`observe-native14-await-discharge.sh` 和 `observe-native14-fcc-status.sh`。现有 Stage 1 计划要求先把 `observe-native14-fcc-status.sh` 复制到设备临时路径 `/tmp/observe-native14-fcc.sh`，再启动 await 脚本；该别名是脚本约定。discharge 记录在电量不高于 10%、电压不高于 3.40 V、温度过限或外部供电恢复时结束记录，不会停止设备放电。旧的 `firmware/observe-full-discharge.sh` 使用 20%/3.45 V 边界且重复读取，不能作为 0–100% 独立验证流程。

`install-kernel-device.sh` 是 native11→native12 的遗留内核工具，严禁用于 native14。请只按 `install-device.sh` 与本包回滚说明处理新候选；当前不执行刷写。
