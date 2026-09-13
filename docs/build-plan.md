# kernel-fedora 设计说明

把上游 [zen-kernel](https://github.com/zen-kernel/zen-kernel) 打成 Fedora RPM，在三个 Copr 工程上构建。本文记录设计取舍、实现方式与踩过的坑；落地文件是按 Copr 工程分目录的三组 spec（`linux-zen-fedora/`、`linux-power/`、`linux-power-lto/`，每组各带一份作为 `Source2` 的 `config`）、`scripts/sync_upstream.py` 与 `.github/workflows/copr-build.yml`。

## 1. 目标与现状

**目标**：持续产出可安装的 zen 内核，上游发新版后自动跟进；同一份上游派生基线 / 省电 / ISA 优化 / LTO 几种变体，彼此可共存。

| 包 | Copr 工程 | `uname -r` | chroot | 特点 |
| --- | --- | --- | --- | --- |
| `kernel-zen` | `binarytree/linux-zen-fedora` | `7.2.4-zen2.fc44.x86_64` | fedora-44 + rawhide | 基线 |
| `kernel-zen-v3` | `binarytree/linux-zen-fedora` | `7.2.4-zen2.v3.fc44.x86_64` | fedora-44 + rawhide | + x86-64-v3 |
| `kernel-power` | `binarytree/linux-power` | `7.2.4-power2.fc44.x86_64` | fedora-44 + rawhide | 省电档 |
| `kernel-power-v3` | `binarytree/linux-power` | `7.2.4-power2.v3.fc44.x86_64` | fedora-44 + rawhide | 省电 + v3 |
| `kernel-power-lto` | `binarytree/linux-power-lto` | `7.2.4-power2.lto.fc44.x86_64` | fedora-44 | 省电 + v3 + ThinLTO |

每个包都产 `-core` / `-modules` / `-devel` / `-devel-matched` 子包。

## 2. 上游与源码组合

Source0 = kernel.org 原版 `linux-7.2.4.tar.xz`，Source1 = zen 的 `linux-v7.2.4-zen2.patch.zst`，Source2 = 与 spec 同目录的 `config`（Arch 官方 linux-zen 的 config；rpkg 按 spec 所在目录解析 Source，所以三个工程目录各有一份、由 CI 同步写入）；`%prep` 用 `zstd -dc %{SOURCE1} | patch -p1` 应用补丁。

不采用 CachyOS 那种「GitHub tag 归档」作源码：与 Arch 官方 `linux-zen` 同源（kernel.org 原版 + zen 补丁 + Arch config）因而行为可对齐；zen 补丁只有 ~150KB，可人工审阅；GitHub 的动态 tag 归档不是稳定发布的固定文件。config 放仓库而不在构建时现拉，是为了可 review、可复现——构建结果不依赖 Arch main 分支当时的提交，只有内核升版对齐时才由 CI 刷新。

## 3. 命名与并存

```spec
%global _tag    v%{_basekver}.%{_stablekver}-zen%{_zenrel}   # v7.2.4-zen2
Version:        %{_basekver}.%{_stablekver}                  # 7.2.4
Release:        zen%{_zenrel}%{?dist}                        # zen2.fc44
%global _kver   %{version}-%{release}.%{_arch}               # 7.2.4-zen2.fc44.x86_64
```

变体只动 `Release` 前缀：baseline 是 `zen%{_zenrel}` / `power%{_zenrel}`，v3 追加 `.v3`，LTO 追加 `.lto`。

- `%build` 用 `make EXTRAVERSION=-%{release}.%{_arch}` 覆盖 zen 补丁在 `Makefile` 里设的 `EXTRAVERSION=-zen2`，因此 `uname -r` 与 `_kver` 严格相等。
- **不需要构建计数器**：上游 tag 变了，`Version`（内核升版）或 `Release`（zen 序号）必然变，NEVRA 天然唯一。
- 5 个 `_kver` 互不相同 ⇒ `/lib/modules/<kver>`、`/boot/vmlinuz-<kver>`、`kernel-*-uname-r` provide 都不冲突，可以同时安装；代价是 `/boot` 占用与构建量。`%{?dist}` 让 fc44 与 rawhide 的 `_kver` 也不同，两个 chroot 的产物不会互相覆盖。

## 4. spec 要点

### 4.1 流程

```
%prep    linux-7.2.4 解包 → zstd -dc 解压补丁并 patch -p1 → 落 config
         → Fedora 适配（DEFAULT_HOSTNAME / LSM）→ 变体改动（v3 / LTO / 省电档）
         → olddefconfig → diff -u config .config 打进构建日志
%build   %make_build EXTRAVERSION=-%{release}.%{_arch} [KCFLAGS="…"] all
         %make_build -C tools/bpf/bpftool vmlinux.h feature-clang-bpf-co-re=1
%install vmlinuz + symvers.zst + modules_install(STRIP) + kernel-devel 文件清单
         + build/source 软链 + 桩 initramfs
```

### 4.2 与 CachyOS 参考的差异

| 项 | CachyOS | 本仓库 | 原因 |
| --- | --- | --- | --- |
| 源码 | GitHub tag 归档 | kernel.org tarball + zen 补丁 | 见第 2 节 |
| 补丁应用 | `%autopatch` | `zstd -dc \| patch -p1` | zen 补丁是 `.patch.zst` |
| config | 构建时从仓库拉 | 仓库内 + CI 刷新 | 可复现 |
| ISA 等级 | `--set-val X86_64_VERSION` | `KCFLAGS=-march=x86-64-v3` | 见 5.1 |
| symvers 压缩 | `zstdmt -19` | `zstd -19 -T0` | 少一个兼容入口 |
| 配置继承 | `CACHY` / `SCHED_BORE` | 无 | zen 补丁已含其调度器改动 |
| 包名 | `%{?_lto_args:-lto}` 动态 | 一变体一 spec、硬编码 | 见 5.2 |
| IMA / nvidia-open / modprobed-db | 有 | 未纳入 | 见第 11 节 |

### 4.3 Fedora 适配（在 Arch config 之上）

| 项 | 处理 |
| --- | --- |
| `CONFIG_DEFAULT_HOSTNAME="archlinux"` | 取消设置 |
| `CONFIG_LSM` 无 selinux | 加 `selinux`，顺序与 Fedora 官方一致 |
| 模块压缩 `MODULE_COMPRESS_ZSTD=y` | 不用改，与 Fedora 一致 |
| `MODULE_SIG_ALL=y` + 构建时一次性密钥 | 不用改；`MODULE_SIG_FORCE` 未开，akmods/dkms 的未签名模块照常加载 |
| `DEBUG_INFO` + DWARF5 + BTF | 保留（BTF 是 `bpftool vmlinux.h` 与 BPF CO-RE 的前提），代价是构建更慢 |

### 4.4 Rust

Arch config 是 `CONFIG_RUST=y`；内核 `scripts/min-tool-version.sh` 要求 rustc ≥ 1.85.0、bindgen ≥ 0.71.1，而 Fedora 44 / rawhide 给的是 rustc 1.98.1 / bindgen 0.72.1，都满足，因此 **`_build_rust 1`（开启）**，BuildRequires 用 Fedora kernel.spec 的同款写法（`rust` / `rust-src` / `bindgen`）。改回 0 即关，`%prep` 会自动 `scripts/config -d RUST`。

## 5. 变体实现

### 5.1 x86-64-v3（`kernel-zen-v3` / `kernel-power-v3` / `kernel-power-lto`）

```spec
%global _x86_64_lvl  3
%global _kcflags     -march=x86-64-v%{_x86_64_lvl}
%make_build EXTRAVERSION=-%{release}.%{_arch} KCFLAGS="%{_kcflags}" all
```

**为什么不用 CachyOS 的 `CONFIG_X86_64_VERSION`**：它不是 mainline 选项，而是 [graysky2/kernel_compiler_patch](https://github.com/graysky2/kernel_compiler_patch) 往 `arch/x86/Kconfig.cpu` 加的 Kconfig 项，外加 `arch/x86/Makefile` 里的 `-march=x86-64-v$(CONFIG_X86_64_VERSION)`。CachyOS 的源码/补丁集带这个 patch，**zen-kernel 不带**，所以那行 `scripts/config` 在这里是静默 no-op（见 6.1）。`KCFLAGS` 的注入点与它等价：顶层 `Makefile` 的 `KBUILD_CFLAGS += $(KCFLAGS)` 在 `include arch/x86/Makefile` 之后执行。代价是它只出现在编译命令行、`.config` 里看不到，「看 config diff」那套判据对 v3 不适用（见第 9 节）。

### 5.2 clang + ThinLTO（`kernel-power-lto`）

与 CachyOS `kernel-cachyos-lto.spec` 逐项对齐：`%define make_build make %{?_lto_args} %{?_smp_mflags}`，参数 `CC=clang CXX=clang++ LD=ld.lld LLVM=1 LLVM_IAS=1`，Kconfig 用 `scripts/config -e LTO_CLANG_THIN`；`BuildRequires: clang`/`lld`/`llvm`（`gcc` 保留），devel 包 `Requires: clang`/`lld`/`llvm`（非 LTO 包则是 `gcc`）；`olddefconfig` 必须用 `%make_build`（见 6.2）。

包名是有意不同的：CachyOS 靠 `_lto_args` 有没有定义动态改 `Name:`，一份 spec 兼产两种包；本仓库的 Copr 用 `buildscm --spec` 区分 package，其它变体也都是「一变体一 spec」，所以硬编码 `Name: kernel-power-lto`。

### 5.3 省电档（`kernel-power` / `-v3` / `-lto`）

相对 linux-zen 只改两处（都在 `%prep` 里，符号均核实存在于本内核 config）：

- `CONFIG_HZ`：1000 → **300**（`%global _hz_tick`；choice 成员 `HZ_100/250/300/1000` 都在）；
- 抢占模型：`CONFIG_PREEMPT=y`（full）→ **`CONFIG_PREEMPT_LAZY=y`**。upstream 原文是「类似 full 抢占，但不过度抢占 SCHED_NORMAL 任务，从而拿回一部分 voluntary 的吞吐」，正是省电/延迟的平衡点，Fedora 同版本内核默认也是它。`PREEMPT_DYNAMIC` 仍为 y，启动参数 `preempt=none|voluntary|full` 可覆盖。

`CONFIG_PCIEASPM_*` **不动**，保持 BIOS 默认：powersave 能省一点电，但部分机型 PCIe 链路会出兼容性问题（这也是它不作为内核默认值的原因），需要时用启动参数 `pcie_aspm=powersave` 单独开。zen config 本来就省电的部分（`RCU_LAZY`、`WQ_POWER_EFFICIENT_DEFAULT`、SATA LPM、`snd_hda` power save、`schedutil`、`TEO`、MGLRU 等）没有重复设置。

**诚实的边界**：内核配置只是耗电的一环——笔电上 S0ix/固件、`TLP`/`powertop`、屏幕与 WiFi 策略、`*_pstate` governor 的影响通常更大。本包只保证「内核这一层是省电取向」，不承诺续航数字；要量化就在同一台机器上用 `powertop`/`turbostat` 对比。

### 5.4 v3 与向量寄存器

内核 `arch/x86/Makefile` 有 `-mno-sse -mno-mmx -mno-sse2 -mno-3dnow -mno-avx -mno-sse4a`。**实测 GCC 16 与 clang 22 都是显式 `-mno-*` 优先于 `-march=x86-64-v3`**：GCC 的 `-Q --help=target` 显示 `-msse`/`-mavx`/`-mavx2` disabled、`-mbmi`/`-mbmi2`/`-mmovbe`/`-mpopcnt` enabled；clang 的预定义宏里 `__AVX__`/`__AVX2__`/`__SSE__` 未定义，而 `__BMI__`/`__BMI2__`/`__MOVBE__`/`__POPCNT__`/`__LZCNT__` 已定义。所以 v3 只带来整数类新指令，内核不会生成 FP/SIMD 代码，**也能与 clang ThinLTO 安全叠加**。mainline 的 `CONFIG_X86_NATIVE_CPU` 是在同一位置追加 `-march=native`，机制相同。Rust 侧没有跟着设 `KRUSTFLAGS`（`KBUILD_RUSTFLAGS` 本来就是 `-Ctarget-cpu=x86-64`，Rust 代码在内核里占比很小）。

## 6. 静默失效陷阱汇总

这一类的共同点：**命令执行成功、退出码 0、没有任何报错，但配置根本没生效**。已经踩到三次：

| # | 写法 | 为什么失效 | 正确做法 |
| --- | --- | --- | --- |
| 6.1 | `scripts/config --set-val X86_64_VERSION 3` | 符号不存在（只有外挂 patch 才有） | 用 `KCFLAGS=-march=x86-64-v3`（5.1） |
| 6.2 | LTO 包里用裸 `make olddefconfig` | `HAS_LTO_CLANG` 由 Kconfig 按 `$(CC)` 当场探测，gcc 下判定为 n，`LTO_CLANG_THIN` 被丢弃 | 用 `%make_build olddefconfig`（自带 `_lto_args`） |
| 6.3 | `-d PREEMPT -e PREEMPT_VOLUNTARY` | x86 上 `PREEMPT_VOLUNTARY` 依赖 `!ARCH_HAS_PREEMPT_LAZY`，写进去会被丢弃 | 先 `sed -i '/^CONFIG_PREEMPT=/d' .config`，再 `-e PREEMPT_LAZY` |

**通用判据**：某项配置是否真的生效，看构建日志里 `%prep` 打出的 `diff -u config .config`，不要只看 `scripts/config` 的命令行。

## 7. 签名与外部模块

**签名在安装时于本机完成，不在构建里签**：私钥只存在于用户机器的 `/etc/pki/akmods/private/private_key.priv`，Copr 沙箱里没有。`%posttrans core` 在 `kernel-install` 之后执行：优先用 `certs/public_key.pem`、回退 `.der`（Fedora 的 `kmodgenca` 默认只生成 `.der`，两种 `sbsign` 都接受）；证书与私钥都在且 `sbsign` 可用时，对 `/boot/vmlinuz-<kver>`（grub 布局）或 `/boot/*/<kver>/linux`（BLS 布局）签名；缺密钥或缺 `sbsign` 时打印 `NOTE:`/`WARNING:` 跳过，**不会让安装失败**，但此时 Secure Boot 机器无法启动该内核。公钥要先注册进 MOK（一次）：`sudo mokutil --import /etc/pki/akmods/certs/public_key.der`。树内模块由内核自己的 `MODULE_SIG_ALL` 用构建时的一次性密钥签名，与此无关。

**外部模块（akmods/dkms）**要装匹配的 `kernel-*-devel-matched`；依赖跟着工具链走——非 LTO 包是 `Requires: gcc`，而 `kernel-power-lto-devel` 是 `Requires: clang`/`lld`/`llvm`（内核用 clang + LTO 编，模块必须同工具链）。

**不产出 `kernel-headers`**：Fedora 官方包已占用 `/usr/include/linux`、`/usr/include/asm`，再出一份会 file conflict（二者只能装一个），替换它又会影响 glibc 等用户空间构建；外部模块编译用 `-devel` 就够。同样不产出 `kernel-debuginfo`。

## 8. Copr 工程与自动化

| 工程 | chroot | package |
| --- | --- | --- |
| `binarytree/linux-zen-fedora` | fedora-44 + rawhide | kernel-zen、kernel-zen-v3 |
| `binarytree/linux-power` | fedora-44 + rawhide | kernel-power、kernel-power-v3 |
| `binarytree/linux-power-lto` | fedora-44 | kernel-power-lto |

公共设置：`enable_net=on`（kernel.org tarball 在 rpkg 生成 SRPM 阶段下载）、`follow_fedora_branching=off`、`module_hotfixes=off`、`multilib=off`、`appstream=off`、`auto_prune=on`。

**自动化**：`copr-build.yml` 每天 03:17 UTC 跑 `scripts/sync_upstream.py`——它读 GitHub release，只认 `vX.Y.Z-zenN` 且必须带 `linux-<tag>.patch.zst` 附件（找不到就报错退出，不会静默用旧版本），把 5 份 spec 的四个版本宏一起更新、并把新 config 同步写入三个工程目录；有 diff 就提交（`[skip ci]`），随后用 `copr-cli buildscm --type git --method rpkg` 按矩阵（5 个 spec → 3 个工程，带 `--subdir` 指向工程目录）触发构建。需要仓库 secret `COPR_CLI_CONFIG`；手动重跑在 Actions 里勾 `force_build`。

**三个环境坑**：

1. **容器内 IPv6**：容器里 IPv6 不通，Python 的 `getaddrinfo` 又优先返回 AAAA → `copr-cli` 的 API 调用挂在 IPv6 连接上，且表现为「无输出 + 退出码 0」的**假成功**；同一个请求 `curl` 1 秒返回（curl 的 Happy Eyeballs 会自动回退 IPv4）。绕法：强制 IPv4 后用 python-copr 库，或把命令放到宿主机跑。

   ```python
   import socket
   _gai = socket.getaddrinfo
   socket.getaddrinfo = lambda h, p, f=0, t=0, pr=0, fl=0: _gai(h, p, socket.AF_INET, t, pr, fl)
   from copr.v3 import Client
   ```

2. **工程不能改名**：`copr-cli` 没有 rename 子命令，`ProjectProxy` 也没有 rename 方法；改名只能用「新建 + 重建 + 删旧」实现。旧工程连同构建历史一起消失、其 URL 随之失效——现在三个工程统一成 `linux-*` 前缀就是这么来的。

3. **描述格式**：Copr 用受限 Markdown——标题、列表、4 空格缩进代码块、行内代码、粗体、链接都能渲染，**表格不行**（`| a | b |` 会连竖线一起原样显示）。description / instructions 一律用列表代替表格。

**资源预期**：本仓库实测 kernel-zen 首轮 102.3 分钟、kernel-power 120 分钟（单 chroot，含 SRPM 生成）；同类项目 `bieszczaders/kernel-cachyos` 一轮 6 chroot 约 120–128 分钟，Copr 单次构建上限约 5 小时。SRPM 生成阶段（`importing` 状态）对内核包很重——要下载 ~150MB 的 tarball，多个全新 package 并发时会明显排队。

## 9. 验证清单

**构建期**：`%prep` 的 `diff -u config .config` 显示 zen 补丁干净应用（0 fuzz）、变体改动确实出现、Fedora 适配就位；LTO 构建能看到 `CONFIG_LTO_NONE=y` → `CONFIG_LTO_CLANG_THIN=y`；v3 构建日志里出现 spec 主动 echo 的 `Building with KCFLAGS=-march=x86-64-v3`；`%build` 不 OOM、不逼近 5 小时。

**装机后**：`uname -r` 等于对应 `_kver`（本仓库当前是 `7.2.4-{zen2,zen2.v3,power2,power2.v3,power2.lto}.fc44.x86_64`）；`journalctl -k | head` 无模块签名/依赖类错误；LTO 包的 `/lib/modules/<kver>/config` 里有 `CONFIG_LTO_CLANG_THIN=y` 与 `CONFIG_LTO_CLANG=y` 且 `CONFIG_LTO_NONE` 消失（这是 LTO 唯一可直接查证的证据）；akmods/dkms 能对着对应的 `-devel-matched` 编出模块。

**指令级（只对 v3 包）**：

```bash
objdump -d /lib/modules/$(uname -r)/kernel/fs/xfs/xfs.ko | grep -cE '\b(popcnt|andn|bzhi|mulx|shlx)\b'
```

`CONFIG_XFS_FS=m`，挑任一必然存在的模块即可。**必须两个包对比**（v3 与 baseline 的同一个模块），只看非零不够——少量命中可能只是巧合。v3 的收益「小但真实」，不承诺具体数字。

## 10. 风险与回退

| # | 风险 | 处理 |
| --- | --- | --- |
| 10.1 | Arch config 与内核版本错位，新选项取默认值 | `olddefconfig` 兜底 + 每次升版 review config diff |
| 10.2 | 工具链不满足内核要求（pahole/BTF、rustc、clang） | 升级 chroot；或临时关 `DEBUG_INFO_BTF`（并去掉 `bpftool vmlinux.h` 那步）/ `_build_rust 0` |
| 10.3 | 上游改 tag 或资产命名、只发 lqx | 同步脚本报错退出，不会静默用旧版本 |
| 10.4 | `DEBUG_INFO=y` 让构建逼近 5 小时或撑爆磁盘 | 看实测耗时；必要时关调试信息，或降低并行度换内存 |
| 10.5 | ThinLTO 链接很重、构建更慢 | LTO 工程目前只开 fedora-44 一个 chroot |
| 10.6 | Rust + LTO（rustc 与 clang 的 LLVM 版本不一致） | 内核 `rust/Makefile` 有 `ifdef CONFIG_LTO` 专门处理；真失败就 `_build_rust 0` 重试 |
| 10.7 | 老 CPU 上 v3 内核无法启动 | 包名与 `uname -r` 都带 `v3`，grub 里保留 baseline 内核可切回 |
| 10.8 | 安装时未签名（缺密钥 / `sbsign` / MOK 未注册） | `%posttrans` 打印 `NOTE:`；按第 7 节补签 + `mokutil --import` |
| 10.9 | Copr API 抖动、GitHub API 限流 | 手动 `force_build` 重跑；同步脚本支持 `GITHUB_TOKEN`（workflow 已注入） |

**回退**：版本信息全在 git 里，`git revert` 同步提交即可回到上一个内核版本再手动触发构建；临时停自动化就把 workflow 的 `schedule` 注释掉，改手工跑 `scripts/sync_upstream.py` + `copr-cli buildscm`。

## 11. 明确不做的事

- `kernel-headers` 子包（会与 Fedora 自带包文件冲突，见第 7 节）与 `kernel-debuginfo`；
- **构建期**签名（签不了，见第 7 节）；
- RT / lqx 变体；非 x86_64 架构；
- IMA / secure boot 相关 config、nvidia-open 随内核构建、modprobed-db 最小化配置；
- CachyOS 的 `CACHY` / `SCHED_BORE` 开关（zen 补丁已包含其调度器改动）。

## 12. 附录：历史记录

**首轮构建 Copr build 10975417（成功）**：手工一次性 `copr-cli buildscm --commit 5a898f3 --spec kernel-zen.spec --type git --method rpkg`；chroot 为 `fedora-44-x86_64`（当时工程只开了这一个），耗时 **102.3 分钟**；产物是 `kernel-zen-7.2.4-zen2.fc44.x86_64.rpm` 及 `-core` / `-modules` / `-devel` / `-devel-matched` / `.src.rpm`；repodata 里三处 provide 一致（`kernel-core-uname-r` = `kernel-modules-uname-r` = `kernel-devel-uname-r` = `7.2.4-zen2.fc44.x86_64`）。这轮验证的是「spec + config + 上游 7.2.4-zen2」能编过，也确认了 `zstd -dc | patch -p1`、`olddefconfig`、`bpftool vmlinux.h`、`kernel-devel` 文件清单都没有问题。

> ⚠️ 该构建属于**已删除的旧 Copr 工程** `binarytree/zen-kernel-fedora`。工程改名是按「新建 + 重建 + 删旧」做的，旧工程连同它的构建历史已被删除，因此 `https://copr.fedorainfracloud.org/coprs/build/10975417` 与当时的 `results/binarytree/zen-kernel-fedora/…` 路径**都已失效**，保留下来的只有上面这些数据。

**待清理项**：`mycopr/packages/kernel-zen/`（草稿 spec + Arch config，未提交）已被本仓库取代、内容一致，建议删除以免两处漂移。
