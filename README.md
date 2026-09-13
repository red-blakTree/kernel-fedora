# zen-kernel-fedora

[![Copr build status](https://copr.fedorainfracloud.org/coprs/binarytree/zen-kernel-fedora/package/kernel-zen/status_image/last_build.png)](https://copr.fedorainfracloud.org/coprs/binarytree/zen-kernel-fedora/package/kernel-zen/)

把 [zen-kernel](https://github.com/zen-kernel/zen-kernel) 打成 Fedora 的 RPM，并在
[Copr](https://copr.fedorainfracloud.org/coprs/binarytree/zen-kernel-fedora/) 上构建。

打包骨架来自 CachyOS 的 Copr spec（`copr-linux-cachyos`），源码组合方式与
Arch Linux 官方 `linux-zen` 一致：**kernel.org 原版源码 + zen 补丁 + Arch linux-zen config**，
再在 `%prep` 里做 Fedora 适配（SELinux LSM、去掉硬编码主机名）。

**Secure Boot**：内核签名在**安装时**由本机完成——本机有 akmods 密钥
（`/etc/pki/akmods/private/private_key.priv` + `certs/public_key.pem`，Fedora 默认给 `.der`）
且装了 `sbsigntools` 时，`%posttrans` 会自动 `sbsign` `/boot/vmlinuz-<kver>`；公钥需先
`sudo mokutil --import /etc/pki/akmods/certs/public_key.der` 注册进 MOK。条件不满足时打印
`NOTE:` 跳过，此时需关闭 Secure Boot。

设计取舍、验证步骤与风险见 [docs/build-plan.md](docs/build-plan.md)。

## 安装

```bash
sudo dnf copr enable binarytree/zen-kernel-fedora
sudo dnf install kernel-zen
```

升级后重启并选择 zen 内核；`uname -r` 形如 `7.2.4-zen2.fc44.x86_64`。

## 省电内核 kernel-power

同一仓库里还有一份省电向的包，上游与 kernel-zen 完全相同，只改内核配置：

```bash
sudo dnf copr enable binarytree/linux-power
sudo dnf install kernel-power
```

`uname -r` 形如 `7.2.4-power2.fc44.x86_64`——与 zen 包的 `7.2.4-zen2.*` 不同名，**两个内核可以并存**。

相对 linux-zen 的差异只有两处（都写在 spec 里，随时可改回）：

| 项 | linux-zen | kernel-power | 想还原时 |
| --- | --- | --- | --- |
| 调度时钟 `_hz_tick` | 1000 Hz | **300 Hz** | 改 spec 顶部的 `%global _hz_tick` |
| 抢占模型 | full | **lazy** | 启动参数 `preempt=full`（也可 `none`/`voluntary`） |

`CONFIG_PCIEASPM_*` 特意**保持 BIOS 默认**：powersave 能省一点电，但部分机型的 PCIe 链路会出
兼容性问题；需要时用启动参数 `pcie_aspm=powersave` 单独开即可。

### 省电与流畅怎么调（不用重编）

每一项的极端档都留了运行时开关，同一个包可以按场景切：

| 想要 | 怎么做 |
| --- | --- |
| 更流畅（游戏 / 视频会议） | 内核参数加 `preempt=full` |
| 默认（省电与流畅折中） | 不用做任何事：`HZ=300` + `PREEMPT_LAZY` + 保留 zen 的交互调优 |
| 更省电（外出/续航） | 内核参数加 `preempt=none snd_hda_intel.power_save=1 pcie_aspm=powersave` |

这些只影响运行时行为；改 `/etc/default/grub` 的 `GRUB_CMDLINE_LINUX` 后跑一次
`grub2-mkconfig -o /boot/grub2/grub.cfg` 重启即可，换场景不用换内核。

zen 配置里本来就省电的部分（`RCU_LAZY`、`WQ_POWER_EFFICIENT_DEFAULT`、`SATA_MOBILE_LPM_POLICY=3`、
`SND_HDA_POWER_SAVE_DEFAULT=10`、`USB_AUTOSUSPEND_DELAY=2`、`schedutil`、`TEO`、MGLRU）已经开着，
没有重复设置。笔电耗电的大头其实在用户空间（S0ix、TLP/powertop、固件），内核这两项只是其中一环。

内核镜像的签名在**安装时**自动完成：本机有 `/etc/pki/akmods/private/private_key.priv` 与
`/etc/pki/akmods/certs/public_key.pem`（Fedora 默认给的是 `.der`，也支持）且装了 `sbsigntools` 时，
会给 `/boot/vmlinuz-<kver>` 签名（公钥需先 `mokutil --import` 注册进 MOK）。没签名时 Secure Boot 需关闭。
外部模块（akmods/dkms）需要
`kernel-zen-devel`，它由 `kernel-zen-devel-matched` 元包带入。

## v3 架构优化内核 kernel-zen-v3 / kernel-power-v3

同一份上游源码、同一份 `config`，只是内核另外用 `-march=x86-64-v3` 编译，多拿到 BMI1/BMI2、
MOVBE、POPCNT、LZCNT 这类整数指令（内核自带的 `-mno-sse/-mno-avx` 仍然生效，不会用向量寄存器）。

```bash
sudo dnf install kernel-zen-v3       # 省电档则是 kernel-power-v3
```

| 包 | `uname -r` 形如 | CPU 要求 |
| --- | --- | --- |
| `kernel-zen` | `7.2.4-zen2.fc44.x86_64` | 任意 x86-64 |
| `kernel-zen-v3` | `7.2.4-zen2.v3.fc44.x86_64` | x86-64-v3 |
| `kernel-power` | `7.2.4-power2.fc44.x86_64` | 任意 x86-64 |
| `kernel-power-v3` | `7.2.4-power2.v3.fc44.x86_64` | x86-64-v3 |

x86-64-v3 大致对应 Intel Haswell（2013）/ AMD Excavator（2015）及以后的 CPU，可以先用
`/lib64/ld-linux-x86-64.so.2 --help | grep supported` 看自己支持到哪一级。**不支持 v3 的机器上
v3 内核无法启动**，这类机器请继续用不带 `-v3` 的包。

四个包的 `_kver` 互不相同，`/lib/modules/<kver>` 与 `/boot/vmlinuz-<kver>` 都不冲突，可以同时装、
在 grub 里各选一个；代价是每个内核连带 initramfs 要占 `/boot` 一两百 MB。

机制、为什么不照搬 CachyOS 的 `CONFIG_X86_64_VERSION`、以及怎么验证优化真的编进去了，
见 [docs/build-plan.md](docs/build-plan.md) 第 13 节。

## 仓库结构

| 文件 | 作用 |
| --- | --- |
| `kernel-zen.spec` / `kernel-power.spec` | 两份 baseline spec，各自构建 `-core` / `-modules` / `-devel` / `-devel-matched` 子包 |
| `kernel-zen-v3.spec` / `kernel-power-v3.spec` | 同上的 x86-64-v3 变体（见第 13 节） |
| `config` | 内核 config 基线，来自 Arch linux-zen，由同步脚本自动刷新 |
| `scripts/sync_upstream.py` | 读 GitHub release，更新 spec 的版本宏与 `config` |
| `.github/workflows/copr-build.yml` | 每天检查上游；有更新时提交并触发 Copr `buildscm` |

## 版本宏

`kernel-*.spec` 这四份 spec 顶部四个宏由脚本维护，手工改版本时也要一起改：

```spec
%global _majver      7     # kernel.org 目录 v7.x
%global _basekver    7.2   # major.minor
%global _stablekver  4     # stable
%global _zenrel      2     # zen 补丁序号（tag 里的 -zen2）
```

## 本地构建（mock，可选，默认不做）

```bash
sudo dnf install -y mock rpmdevtools rpm-build spectool
sudo usermod -aG mock $USER   # 需要重新登录

rpmspec -P kernel-zen.spec                 # 检查宏展开
spectool -g kernel-zen.spec                # 下载 Source0/Source1 到当前目录
mock -r fedora-44-x86_64 --buildsrpm --spec kernel-zen.spec --sources .
mock -r fedora-44-x86_64 --rebuild ./kernel-zen-*.src.rpm
```

内核构建很吃磁盘和时间（单 chroot 约 1–2 小时），本地跑之前先确认 `/var/lib/mock`
所在分区有足够空间。

## 自动构建

`sync` job 跑 `scripts/sync_upstream.py`：有新版本就提交（`[skip ci]`），
`build` job 再用 `copr-cli buildscm`（`--type git --method rpkg`）触发 Copr 构建。
需要仓库 secret `COPR_CLI_CONFIG`，内容就是 `copr-cli` 的配置文件：

```ini
[copr-cli]
login = <API login>
username = binarytree
token = <API token>
copr_url = https://copr.fedorainfracloud.org
```

手动强制重建：Actions → *Sync zen-kernel and build in Copr* → Run workflow →
勾选 `force_build`。

## 已知限制

- 内核镜像的签名在**安装时**做（`%posttrans`），构建里不签（私钥不在构建环境里）：本机有
  `/etc/pki/akmods/private/private_key.priv` 和 `/etc/pki/akmods/certs/public_key.pem`
  （Fedora 的 `kmodgenca` 默认给 `.der`，同样支持）时，用 `sbsign` 签 `/boot/vmlinuz-<kver>`；
  缺密钥或缺 `sbsign` 就打印 `NOTE:` 跳过。密钥是装完内核后才生成的，手动补一次即可：

  ```bash
  sudo sbsign --key /etc/pki/akmods/private/private_key.priv \
              --cert /etc/pki/akmods/certs/public_key.pem \
              --output /boot/vmlinuz-$(uname -r).signed /boot/vmlinuz-$(uname -r)
  sudo mv /boot/vmlinuz-$(uname -r).signed /boot/vmlinuz-$(uname -r)
  ```

  公钥注册进 MOK（一次）：`sudo mokutil --import /etc/pki/akmods/certs/public_key.der`
- chroot：`fedora-44-x86_64` + `fedora-rawhide-x86_64`；架构只有 x86_64。
- 不产出 `kernel-headers`：Fedora 官方 `kernel-headers` 已经占用 `/usr/include/linux`、`/usr/include/asm`
  等路径，再出一份会文件冲突（两个包只能装一个）；外部模块编译用 `kernel-zen-devel` 就够。
  同样不产出 `kernel-debuginfo`。
- Rust for Linux 默认开启（`%global _build_rust 1`）：内核要求 rustc ≥ 1.85.0、bindgen ≥ 0.71.1，
  Fedora 44 与 rawhide 提供 rustc 1.98.1 / bindgen 0.72.1。
