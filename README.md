# zen-kernel-fedora

[![Copr build status](https://copr.fedorainfracloud.org/coprs/binarytree/linux-zen-fedora/package/kernel-zen/status_image/last_build.png)](https://copr.fedorainfracloud.org/coprs/binarytree/linux-zen-fedora/package/kernel-zen/)

把 [zen-kernel](https://github.com/zen-kernel/zen-kernel) 打成 Fedora 的 RPM 并在 Copr 上构建。源码组合与
Arch Linux 官方 `linux-zen` 一致：**kernel.org 原版 tarball + zen 补丁（`.patch.zst`）+ Arch linux-zen
config**，再在 `%prep` 里做 Fedora 适配（SELinux LSM、去掉硬编码主机名）；打包骨架参考 CachyOS 的
`copr-linux-cachyos`。设计取舍、验证步骤与风险见 [docs/build-plan.md](docs/build-plan.md)。

> 注意：GitHub 仓库名与本地目录名是 `zen-kernel-fedora`，而 Copr 工程名统一为 `linux-*`（历史原因）。

## 安装

```bash
sudo dnf copr enable binarytree/linux-zen-fedora   # zen 系
sudo dnf copr enable binarytree/linux-power        # 省电系
sudo dnf copr enable binarytree/linux-power-lto    # 省电 + ThinLTO
```

| 包 | Copr 工程 | `uname -r` 形如 | 特点 | CPU 要求 |
| --- | --- | --- | --- | --- |
| `kernel-zen` | linux-zen-fedora | `7.2.4-zen2.fc44.x86_64` | zen 默认配置 | 任意 x86-64 |
| `kernel-zen-v3` | linux-zen-fedora | `7.2.4-zen2.v3.fc44.x86_64` | zen + x86-64-v3 | x86-64-v3 |
| `kernel-power` | linux-power | `7.2.4-power2.fc44.x86_64` | 省电档 | 任意 x86-64 |
| `kernel-power-v3` | linux-power | `7.2.4-power2.v3.fc44.x86_64` | 省电档 + x86-64-v3 | x86-64-v3 |
| `kernel-power-lto` | linux-power-lto | `7.2.4-power2.lto.fc44.x86_64` | 省电档 + v3 + clang ThinLTO | x86-64-v3 |

五个包的 `_kver` 互不相同（`/lib/modules/<kver>`、`/boot/vmlinuz-<kver>`、`kernel-*-uname-r` 都不冲突），
**可以同时安装**，重启时在 GRUB 里各选一个；代价是每个内核连带 initramfs 占 `/boot` 一两百 MB。

## 三个变体各改了什么

- **zen**：直接用 Arch linux-zen 的配置，不额外调优。
- **power**：只改两处——`CONFIG_HZ` 1000 → **300**（定时器中断更少），抢占模型 full →
  **`PREEMPT_LAZY`**（类似 full，但不过度抢占 `SCHED_NORMAL`，拿回一部分吞吐）。`CONFIG_PCIEASPM_*`
  特意保持 BIOS 默认（powersave 能省电，但部分机型 PCIe 链路有兼容性问题）。zen 里本来就省电的部分
  （`RCU_LAZY`、`WQ_POWER_EFFICIENT_DEFAULT`、SATA LPM、`snd_hda` power save、`schedutil`、`TEO`、
  MGLRU…）没有重复设置；笔电耗电大头在用户空间，**不承诺具体续航数字**。
- **lto**：在 power 基础上改用 clang + ThinLTO，参数与 CachyOS 的 `kernel-cachyos-lto.spec` 一致
  （`CC=clang CXX=clang++ LD=ld.lld LLVM=1 LLVM_IAS=1` + `CONFIG_LTO_CLANG_THIN`）。ThinLTO 链接很重，
  构建明显慢于其它包。

### v3 是通用维度，不属于某个变体

`-v3` 与上面三个变体正交：spec 里 `%global _x86_64_lvl 3`，`%build` 通过 `KCFLAGS` 传
`-march=x86-64-v3`。内核自带的 `-mno-sse/-mno-avx` 优先于 `-march`（GCC 16 与 clang 22 均实测过），
所以只多拿到 BMI1/BMI2/MOVBE/POPCNT/LZCNT 这类整数指令，不会让内核使用向量寄存器。

x86-64-v3 大致对应 Intel Haswell（2013）/ AMD Excavator（2015）及以后，可先自测
`/lib64/ld-linux-x86-64.so.2 --help | grep supported`。**不支持 v3 的机器上 v3 内核无法启动**，
这类机器请用不带 `-v3` 的包。

## 外部模块与 Secure Boot

外部模块（akmods / dkms / NVIDIA / VirtualBox…）要对准具体内核编译，装对应的 `-devel-matched`
元包即可带入 `-devel`：`sudo dnf install kernel-zen-devel-matched`（换成你还装的那个内核名）。

`kernel-power-lto-devel` 依赖 `clang`/`lld`/`llvm` 而**不是** `gcc`（内核用 clang + LTO 编，模块工具链
必须一致）；其它四个包依赖 `gcc`。

内核镜像**在安装时于本机签名，构建期不签**（私钥不在构建环境里）：本机有 akmods 密钥
（`/etc/pki/akmods/private/private_key.priv` + `certs/public_key.pem`，Fedora 的 `kmodgenca` 默认只给
`.der`，两种都支持）且装了 `sbsigntools` 时，`%posttrans` 会自动 `sbsign` `/boot/vmlinuz-<kver>`；
缺密钥或缺 `sbsign` 就打印 `NOTE:` 跳过，此时 Secure Boot 需关闭（手动补签见 build-plan 第 7 节）。
公钥要先注册进 MOK（一次）：`sudo mokutil --import /etc/pki/akmods/certs/public_key.der`。

## 省电与流畅怎么调（power / -v3 / -lto，不用重编）

| 想要 | 内核参数 |
| --- | --- |
| 更流畅（游戏 / 视频会议） | `preempt=full` |
| 默认（省电与流畅折中） | 不用加：`HZ=300` + `PREEMPT_LAZY` |
| 更省电（外出 / 续航） | `preempt=none snd_hda_intel.power_save=1 pcie_aspm=powersave` |

改 `/etc/default/grub` 的 `GRUB_CMDLINE_LINUX`，跑一次 `sudo grub2-mkconfig -o /boot/grub2/grub.cfg`
重启即可，换场景不用换内核。

## 仓库结构

| 文件 | 作用 |
| --- | --- |
| `linux-zen-fedora/` | kernel-zen、kernel-zen-v3 两份 spec + 该工程的 `config` |
| `linux-power/` | kernel-power、kernel-power-v3 两份 spec + `config` |
| `linux-power-lto/` | kernel-power-lto 一份 spec + `config` |
| `scripts/sync_upstream.py` | 读 GitHub release，更新 5 份 spec 的版本宏，并把新 config 同步写入三个目录 |
| `.github/workflows/copr-build.yml` | 每天检查上游；有更新时提交，并按矩阵（带 `--subdir`）投给 3 个 Copr 工程 |

三个目录各有一份内容完全相同的 `config`（`Source2` 是按 spec 所在目录解析的，必须与 spec 同目录；由同步脚本
一起刷新以保证一致）。每份 spec 顶部四个宏由脚本维护，手工改版本时也要一起改：
`_majver`（kernel.org 的 v7.x）、`_basekver`（7.2）、`_stablekver`（4）、`_zenrel`（zen 补丁序号 2）。

## 本地构建（mock，可选，默认不做）

```bash
sudo dnf install -y mock rpmdevtools rpm-build spectool
cd linux-zen-fedora
rpmspec -P kernel-zen.spec && spectool -g kernel-zen.spec
mock -r fedora-44-x86_64 --buildsrpm --spec kernel-zen.spec --sources .
mock -r fedora-44-x86_64 --rebuild ./kernel-zen-*.src.rpm
```

内核构建很吃磁盘和时间（单 chroot 约 1–2 小时，LTO 更久），先确认 `/var/lib/mock` 所在分区空间够用。

## 自动构建

`sync` job 跑 `scripts/sync_upstream.py`：有新版本就提交（`[skip ci]`），`build` job 再用
`copr-cli buildscm`（`--type git --method rpkg`）投给三个 Copr 工程。需要仓库 secret
`COPR_CLI_CONFIG`（`copr-cli` 配置文件的内容）。手动强制重建：Actions →
*Sync zen-kernel and build in Copr* → Run workflow → 勾选 `force_build`。

## 已知限制

- chroot：`linux-zen-fedora` 与 `linux-power` 是 `fedora-44-x86_64` + `fedora-rawhide-x86_64`，
  `linux-power-lto` 目前只有 `fedora-44-x86_64`；架构只有 x86_64。
- 不产出 `kernel-headers`：Fedora 官方 `kernel-headers` 已占用 `/usr/include/linux`、`/usr/include/asm`
  等路径，再出一份会文件冲突；外部模块用 `-devel` 就够。同样不产出 `kernel-debuginfo`。
- Rust for Linux 默认开启（`%global _build_rust 1`）：内核要求 rustc ≥ 1.85.0、bindgen ≥ 0.71.1，
  Fedora 44 与 rawhide 提供 rustc 1.98.1 / bindgen 0.72.1。
- v3 / lto 变体对 CPU 有要求（见上）；lto 包的外部模块必须用 clang。
