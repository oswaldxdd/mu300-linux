# U30 Air native14 restorefix2 候选包

此目录对应镜像版本 `u30air-native14-restorefix2-2026.10.07-test`。这是离线审查候选包；当前设备上运行的 native14 和已记录的充电端点观测来自更早的镜像，本候选包尚未刷入、启动或进行硬件充电验收。

包内保留与 `7.2.8-u30air-native14` 完全匹配的已编译内核归档，未重新编译内核，也未改变驱动算法。rootfs 中只将 `/etc/mu300/image-version` 更新为 restorefix2 版本；旧版本中其余 rootfs 成员逐项保持相同。FCC restore library 和 service 的实际文件字节分别为 SHA-256 `afc7805eab8531142e7c2b6e04d0bc94932aa2539b57edf8e73003ecea141000` 和 `6f5c63cf61440b6ba2d7f64989c66a198743f5ad93e71252a187a5ed3d693da1`。

新安装器支持相同 KREL、不同镜像版本的受限升级：逐路径比较旧模块与候选模块，并只允许候选 rootfs 多出的 `modules.builtin` 和 `modules.builtin.modinfo`，且两者必须逐字节匹配已校验的内核归档。完全相同的镜像版本仍拒绝重复安装。回滚包会校验并恢复完整的旧模块快照。安装器把已校验的候选镜像版本传给 `mu300-update`，避免 boot 元数据保留旧版本标签。`test-deployment-compat.py` 在 WSL 中以临时普通文件运行了 39 个事务与失败注入场景，其中包含 staging 目录 `ls -id`/awk 身份读取失败时的 fail-closed 清理检查；成功升级场景也断言 updater 收到 `local-<候选版本>`。该离线运行用 Python JSON shim 代替 WSL 未提供的 OpenWrt `jsonfilter`。设备端曾对纯内存 JSON fixture 验证 `jsonfilter` 的字符串与类型提取，但尚未解析 restorefix2 完整 manifest。主审另在设备只读核对 `/` 与 `/mnt/mu300-disk/openwrt` 的 `ls -id` 均解析到 inode `172038`。WSL 测试、替身、shim 和静态检查都不等于 U30 Air 实机安装或回滚。

`observe-charge-to-full.sh` 是只读观察器，不会控制充电或电源状态。它每轮检查 boot ID，并记录电量、两侧 Full/Good、外部供电、CV、OCV、电流和温度；只有 100% 与硬件 Full、Good、外部供电在线、CV/OCV/电流/温度门限同时连续满足至少 120 秒才记录合格满电端点。它不自动启动，也不用于独立容量精度验收。

早先 native14 运行时曾记录到稳定 100%/Full 端点以及 3983mAh 学习模型恢复；这不证明 restorefix2 包已启动。独立放电观察只到 30% 后因温度边界结束，低电量端及全范围电量精度仍未独立验证。清单中的 `hardware_boot_tested`、`hardware_charging_tested` 和 `independent_capacity_accuracy_verified` 均为 `false`。

源包中的 `install-kernel-device.sh` 是 native11→native12 的历史工具，目标内核为 native12；严禁将其用于 native14。native14 应使用完整固件事务路径，先完成设备身份、当前镜像/KREL、资源、DTBO、空间、包哈希及恢复备份检查。不要将离线测试报告解释为部署授权或设备端成功证据。
