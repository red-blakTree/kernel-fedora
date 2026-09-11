# zen-kernel-fedora

[![Copr build status](https://copr.fedorainfracloud.org/coprs/binarytree/zen-kernel-fedora/package/kernel-zen/status_image/last_build.png)](https://copr.fedorainfracloud.org/coprs/binarytree/zen-kernel-fedora/package/kernel-zen/)

把 [zen-kernel](https://github.com/zen-kernel/zen-kernel) 打成 Fedora 的 RPM，并在
[Copr](https://copr.fedorainfracloud.org/coprs/binarytree/zen-kernel-fedora/) 上构建。

打包骨架来自 CachyOS 的 Copr spec（`copr-linux-cachyos`），源码组合方式与
Arch Linux 官方 `linux-zen` 一致：**kernel.org 原版源码 + zen 补丁 + Arch linux-zen config**，
再在 `%prep` 里做 Fedora 适配（SELinux LSM、去掉硬编码主机名）。

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

相对 linux-zen 的差异只有三处（都写在 spec 里，随时可改回）：

| 项 | linux-zen | kernel-power | 想还原时 |
| --- | --- | --- | --- |
| 调度时钟 `_hz_tick` | 1000 Hz | **250 Hz** | 改 spec 顶部的 `%global _hz_tick` |
| 抢占模型 | full | **voluntary** | 启动参数 `preempt=full`（也可 `lazy`） |
| PCIe ASPM | BIOS 默认 | **powersave** | 启动参数 `pcie_aspm=default` |

zen 配置里本来就省电的部分（`RCU_LAZY`、`WQ_POWER_EFFICIENT_DEFAULT`、`SATA_MOBILE_LPM_POLICY=3`、
`SND_HDA_POWER_SAVE_DEFAULT=10`、`USB_AUTOSUSPEND_DELAY=2`、`schedutil`、`TEO`、MGLRU）已经开着，
没有重复设置。笔电耗电的大头其实在用户空间（S0ix、TLP/powertop、固件），内核这三项只是其中一环。

**Secure Boot 必须关闭**（内核未签名）。外部模块（akmods/dkms）需要
`kernel-zen-devel`，它由 `kernel-zen-devel-matched` 元包带入。

## 仓库结构

| 文件 | 作用 |
| --- | --- |
| `kernel-zen.spec` | 唯一的 spec，构建 `kernel-zen` / `-core` / `-modules` / `-devel` / `-devel-matched` |
| `config` | 内核 config 基线，来自 Arch linux-zen，由同步脚本自动刷新 |
| `scripts/sync_upstream.py` | 读 GitHub release，更新 spec 的版本宏与 `config` |
| `.github/workflows/copr-build.yml` | 每天检查上游；有更新时提交并触发 Copr `buildscm` |

## 版本宏

`kernel-zen.spec` 顶部四个宏由脚本维护，手工改版本时也要一起改：

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

- 内核默认未签名：只有构建环境里存在 `/etc/pki/akmods/certs/public_key.der` 与
  `/etc/pki/akmods/private/private_key.priv` 时，才会用 `sbsign` 自动签名 vmlinuz。COPR 的构建
  沙箱没有这两个文件（它们在你本机），所以 **COPR 产物仍是无签名内核，Secure Boot 需关闭**；
  想要带签名的内核就用本地 mock 构建，并在构建前生成好 akmods 密钥。
- chroot：`fedora-44-x86_64` + `fedora-rawhide-x86_64`；架构只有 x86_64。
- 不产出 `kernel-headers`：Fedora 官方 `kernel-headers` 已经占用 `/usr/include/linux`、`/usr/include/asm`
  等路径，再出一份会文件冲突（两个包只能装一个）；外部模块编译用 `kernel-zen-devel` 就够。
  同样不产出 `kernel-debuginfo`。
- Rust for Linux 默认开启（`%global _build_rust 1`）：内核要求 rustc ≥ 1.85.0、bindgen ≥ 0.71.1，
  Fedora 44 与 rawhide 提供 rustc 1.98.1 / bindgen 0.72.1。
