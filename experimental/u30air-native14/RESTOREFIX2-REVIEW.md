# restorefix2 离线审查记录

候选镜像：`u30air-native14-restorefix2-2026.10.07-test`  
内核：`7.2.8-u30air-native14`  
状态：新包仅离线组装和核验，未刷入 U30 Air。

## 变更与来源

- 复用旧候选包中已编译的 kernel tar.gz 原始字节；没有重建内核或改变驱动算法。
- 新 rootfs 从旧包逐成员复制，只把 `etc/mu300/image-version` 改为 restorefix2。部署兼容性审计允许同 KREL 下新旧镜像升级的条件保持严格；精确同版本拒绝，模块快照由回滚器完整校验和恢复。
- 使用已审安装器、回滚器和 39 场景离线测试脚本；对应 SHA-256 为：

```text
install-device.sh          10eb29b03965be8593f3e5c9297e6a7670dc7ec00413a5f21dfdf8db00de2697
rollback-device.sh         4511f7e3559124d6987e5f2e3e0e3f702dbe4e27485ece9fefe28b6e58af5f5e
test-deployment-compat.py  a22a996a818aabd4af5635651546c4748145ca21a59f45e6e6b4cb5ebd6a1b31
observe-charge-to-full.sh  d32f92573fba077912b9f9fb781eaa555065d9ff9c04efc4fdba2f8b1e2857ce
```

- 候选 rootfs 及随附源树包含 FCC restore library/service 的实际字节，哈希分别为 `afc7805eab8531142e7c2b6e04d0bc94932aa2539b57edf8e73003ecea141000` 与 `6f5c63cf61440b6ba2d7f64989c66a198743f5ad93e71252a187a5ed3d693da1`。
- 原 native14 部署兼容性审计、WSL 测试记录、设备 31 模块清单以及此前 native14 满电端点的只读证据随 release 一并归档。
- 实机只读依赖核对中，`ls -id`/awk 解析 `/` 与 `/mnt/mu300-disk/openwrt` 均得到 inode `172038`；不含实机写入。

## 设备证据的边界

此前运行的 native14 镜像（版本 `u30air-native14-android-charge-2026.10.05-test`）记录到 13 个样本、120.54 秒的 100%/Full 稳定端点；当时记录的 FCC 学习模型为 3983mAh 且重启后恢复。此证据属于既有运行时，不属于本 restorefix2 包。restorefix2 当前没有硬件启动、蜂窝/Wi-Fi、网卡、充电或回滚测试结论。

先前独立放电观察在 30%/45°C 温度边界停止。尚无低电量端完整记录，也未完成独立全电量范围容量精度验证；不能用 FCC 同一传感器电流积分一致性或 WSL fixture 来替代。

## 离线检查范围

组装器检查输入旧 release 的哈希和嵌套清单，单独生成 restorefix2 目录及 ZIP，并拒绝覆盖旧或已存在的新产物。交付 verifier 检查内外清单、版本、源归档、31 个共同模块、built-in 双索引来源、FCC runtime 字节、严格只读 observer、资源保留静态保护和私密文件排除。模块树及硬件状态请以本目录关联的审计报告和测试日志为准。

WSL 测试以临时普通文件和替代更新器模拟事务；WSL 没有 OpenWrt `jsonfilter`，该 39 场景运行中的 manifest 提取使用 Python JSON shim。staging `ls -id`/awk 失败注入验证无法读取目录身份时 fail-closed；设备只读样本确认 `/` 与 `/mnt/mu300-disk/openwrt` 返回的 inode 均为 `172038`。设备端曾用 `jsonfilter` 对纯内存 JSON fixture 验证字符串及类型提取；尚未用设备 `jsonfilter` 解析 restorefix2 完整 manifest。离线测试验证脚本分支和回滚事务，不打开真实 boot 分区，也不证明目标设备的运行时复制、启动、充电或硬件恢复。

源包中的 `install-kernel-device.sh` 明确是 native11→native12 旧工具，不能用于 native14。只有主审完成新归档复核并另行决定后，才可考虑设备端部署。
