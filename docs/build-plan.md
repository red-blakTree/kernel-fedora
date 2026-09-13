# 在 Copr 上编译 zen-kernel 的方案

本文是 `zen-kernel-fedora` 仓库的设计说明：目标、参考实现分析、关键取舍、spec 逐段要点、
Copr 工程配置、验证流程与风险。落地文件见仓库根目录。

## 1. 目标与成功标准

**目标**：在 Copr 上持续产出可安装的 `kernel-zen` RPM（x86_64、Fedora 当前稳定版 + rawhide），
上游 zen-kernel 发布新版本后能自动跟进。

**成功标准**（按顺序验证，全部可独立复核）：

1. ✅ **已达成**：Copr 构建（build 10975417）成功产出 SRPM 与 5 个子包，见第 11 节验证记录。
   本地 `rpmspec -P` 按约定不做，等价检查由 Copr 的 SRPM 构建承担。
2. ✅ **已达成**：产出 `kernel-zen-7.2.4-zen2.fc44.x86_64.rpm` 与
   `-core` / `-modules` / `-devel` / `-devel-matched` 子包。
3. ✅ **已达成**：repodata 中 `kernel-zen-core` 提供 `kernel-core-uname-r = 7.2.4-zen2.fc44.x86_64`，
   与 `_kver` 宏的推导一致。
4. ⏳ **待真机验证**：安装并重启后 `uname -r` 等于上述 `_kver`；`journalctl -k | head` 无模块签名/依赖类错误。
5. ⏳ **待真机验证**：`kernel-zen-devel-matched` 能支撑外部模块构建（akmods/dkms 实测通过）。
6. ⏳ **待配置验证**：上游出新 tag 后 workflow 自动改 spec 宏 + `config` 并触发构建（需先配 `COPR_CLI_CONFIG`）。

**明确的非目标**：产出**已签名**的 RPM（签名改由安装时在本机用 akmods 密钥完成，见 5.5）、
`kernel-headers`、`kernel-debuginfo`、LTO/clang 变体、RT/lqx 变体、多架构。

## 2. 参考实现的可用部分

| 参考 | 提供什么 | 本方案怎么用 |
| --- | --- | --- |
| `mycopr` | 「CI 检测上游 → 改 spec `%global` → `copr-cli buildscm --type git --method rpkg`」整套流程；GitHub Actions 的 secret/提交方式 | 流程照搬，但**不**接入 `packages.toml`（原因见 2.1），改为独立仓库 + 专用脚本 |
| `copr-linux-cachyos` | 已验证的 Fedora 内核打包骨架：脚本段（`%posttrans` 调 `kernel-install`）、`kernel-devel` 的完整文件清单、桩 initramfs、`_disable_source_fetch 0` | spec 骨架逐段沿用，只改与 zen 相关的部分（见 5.2 差异表） |
| Arch `linux-zen` | 源码组合（kernel.org 原版 + zen 补丁 + Arch config）与版本命名（`7.2.4-zen2`） | Source0/Source1/Source2 与版本宏方案直接采用 |

### 2.1 为什么不走 mycopr 的 `packages.toml`

`mycopr` 的 `transforms` 只支持 `dot` / `strip_v` / `strip:TEXT` 三种字符串操作，
而内核需要一个 tag 拆成**四个**宏：

```
v7.2.4-zen2  ->  _majver=7, _basekver=7.2, _stablekver=4, _zenrel=2
```

要给内核加 transform 就得改 `mycopr/scripts/common.py` 这个所有包共用的核心，
收益（省一个脚本）小于影响面。另外 `buildscm` 只能构建 mycopr 自身仓库里的 spec，
把内核放进 `mycopr/packages/kernel-zen/` 会把 150MB 级源码构建和几十个轻量包混在同一仓库的
CI 里。因此：**独立仓库 + 独立同步脚本**。

## 3. 仓库结构

```
zen-kernel-fedora/
├── kernel-zen.spec                  # 唯一 spec
├── config                           # 内核 config 基线（Arch linux-zen，脚本自动刷新）
├── scripts/sync_upstream.py         # 上游检测：改 spec 宏 + 刷 config
├── .github/workflows/copr-build.yml # 每天同步；有更新则提交并触发 Copr
├── docs/build-plan.md               # 本文
└── README.md
```

## 4. 版本与命名

```spec
%global _tag    v%{_basekver}.%{_stablekver}-zen%{_zenrel}   # v7.2.4-zen2
Version:        %{_basekver}.%{_stablekver}                  # 7.2.4
Release:        zen%{_zenrel}%{?dist}                        # zen2.fc44
%global _kver   %{version}-%{release}.%{_arch}               # 7.2.4-zen2.fc44.x86_64
```

- uname 里带 `%{?dist}` 和 `_arch`，与 CachyOS 的做法一致；`%build` 用
  `make EXTRAVERSION=-%{release}.%{_arch}` 覆盖 zen 补丁在 `Makefile` 里设的 `EXTRAVERSION=-zen2`，
  因此 `uname -r` 与 `_kver` 严格相等。
- **不需要构建计数器**：上游 tag 变了，`Version`（内核升版）或 `Release`（zen 序号 2→3）必然变，
  NEVRA 天然唯一（`zen%{_zenrel}` 就是这个作用）。
- `%{?dist}` 让 fc44 与 rawhide 的 `_kver` 不同，两个 chroot 的产物不会互相覆盖。
- `kernel-power` 用同一套宏推导，只把 `Release` 前缀换成 `power%{_zenrel}`，于是
  `uname -r` 是 `7.2.4-power2.fc44.x86_64`，与 zen 包不同名、可以并存（见第 12 节）。

## 5. spec 要点

### 5.1 编译流程

```
%prep    linux-7.2.4 解包 → zstd -dc 解压 zen 补丁并 patch -p1 → 落 config
         → Fedora 适配（DEFAULT_HOSTNAME / LSM / RUST）→ make olddefconfig → 打印 config 差异
%build   make EXTRAVERSION=-%{release}.%{_arch} all
         make -C tools/bpf/bpftool vmlinux.h feature-clang-bpf-co-re=1
%install vmlinuz + symvers.zst + modules_install(STRIP) + kernel-devel 文件清单
         + build/source 软链 + 桩 initramfs
```

### 5.2 与参考实现的差异（含原因）

| 项 | CachyOS spec | 本方案 | 原因 |
| --- | --- | --- | --- |
| 源码 | GitHub tag 归档 `CachyOS/linux` | kernel.org 原版 tarball + zen 补丁 | 与 Arch 官方 linux-zen 同源；补丁只有 150KB 且可人工审阅；kernel.org tarball 是稳定发布的固定文件，不依赖 GitHub 动态生成归档 |
| 补丁应用 | `%autopatch`（普通 `.patch`） | `zstd -dc %{SOURCE1} \| patch -p1` | zen 补丁是 `.patch.zst`，显式解压避免依赖 rpmbuild 对压缩补丁的处理 |
| config | 构建时从 linux-cachyos 仓库拉 | 仓库内 `config` + 由 CI 从 Arch 刷新 | 可 review、可复现（构建不依赖 Arch main 分支当时的提交） |
| ISA 等级 | `scripts/config --set-val X86_64_VERSION` | 删除 | 实测该内核 config 里**不存在** `CONFIG_X86_64_VERSION`（`grep` 结果为 0 命中），原写法是无效设置。需要 ISA 优化时应在 `%build` 用 `KCFLAGS` 传 `-march=x86-64-v3`（已在第 13 节的 v3 包中落地） |
| symvers 压缩 | `zstdmt -19` | `zstd -19 -T0` | 同一 `zstd` 包提供，避免依赖 `zstdmt` 这个兼容入口 |
| 配置继承 | 自身 config + `CACHY`/`SCHED_BORE` switch | 无 | zen 补丁已包含其调度器改动，没有 `CACHY` 这类开关 |
| Rust | 关闭 | 关闭，但做成 `_build_rust` 开关 | 见 5.4 |
| 其他 | IMA/secure boot、nvidia-open、LTO、modprobed-db 最小化 | 未纳入 | 与当前目标无关；需要时按 CachyOS spec 对应分支再加 |

> 附带修正：`mycopr/packages/kernel-zen/` 里那份草稿用 `%setup -n zen-kernel-%{_tag}`，
> 而 GitHub tag 归档的顶层目录会去掉 `v` 前缀（`zen-kernel-7.2.4-zen2`），两者不匹配；
> 本方案改用原版 tarball 后，`%setup -n linux-7.2.4` 没有这个歧义。

### 5.3 Fedora 适配（在 Arch config 之上）

| 项 | Arch 原值 | Fedora 适配 |
| --- | --- | --- |
| `CONFIG_DEFAULT_HOSTNAME` | `"archlinux"` | 取消设置（回落到 `localhost`） |
| `CONFIG_LSM` | `landlock,lockdown,yama,integrity,bpf` | 加 `selinux`，顺序与 Fedora 官方一致 |
| `CONFIG_RUST` | `y` | 默认关（见 5.4） |
| 模块压缩 | `CONFIG_MODULE_COMPRESS_ZSTD=y` | 不用改，与 Fedora 一致 |
| 模块签名 | `MODULE_SIG_ALL=y`，`MODULE_SIG_KEY="certs/signing_key.pem"`，`MODULE_SIG_FORCE` 未开 | 不用改：构建时用 `openssl` 生成一次性密钥签名树内模块；因为 `MODULE_SIG_FORCE` 未开，akmods/dkms 的未签名模块照常加载 |
| `SYSTEM_TRUSTED_KEYS` | 空 | 不用改，不会去找 Arch 的密钥文件 |
| 调试信息 | `DEBUG_INFO=y` + DWARF5 + BTF | 保留（BTF 是 `bpftool vmlinux.h` 和 BPF CO-RE 的前提），代价是构建更慢；见风险 8.4 |

`%prep` 末尾的 `diff -u config .config` 会把每次 `olddefconfig` 的实际改动打进构建日志，
内核升版本时这是最省事的 review 入口。

### 5.4 Rust（已开启）

Arch config 是 `CONFIG_RUST=y`，而内核 `scripts/min-tool-version.sh`（v7.2.4-zen2）要求
**rustc ≥ 1.85.0、bindgen ≥ 0.71.1**；Fedora 44 与 rawhide 提供的是 **rustc 1.98.1 / bindgen 0.72.1**，
两个 chroot 都满足，因此 `%global _build_rust 1`，BuildRequires 用 Fedora kernel.spec 的同款写法
（`rust` / `rust-src` / `bindgen`）。要关掉就把宏改回 0（`%prep` 会自动 `scripts/config -d RUST`）。

### 5.5 内核签名：放在安装时做，不在构建里签

私钥只在**你自己机器**的 `/etc/pki/akmods/private/private_key.priv`，构建环境（含 Copr 沙箱）里没有，
因此 spec 里**没有**构建期签名；改为在 `%posttrans core` 里、`kernel-install` 之后执行：

1. 选证书：优先 `/etc/pki/akmods/certs/public_key.pem`，找不到则用 `.../public_key.der`
   （Fedora 的 `kmodgenca` 默认只生成 `.der`——读 `/usr/bin/kmodgenca` 确认过；两种格式 `sbsign` 都接受）。
2. 证书与私钥都在、且 `sbsign` 可用时，对 `/boot/vmlinuz-<kver>`（grub 布局）或
   `/boot/*/<kver>/linux`（BLS 布局）执行：

   ```bash
   sbsign --key /etc/pki/akmods/private/private_key.priv \
          --cert /etc/pki/akmods/certs/public_key.pem \
          --output <镜像>.signed <镜像>
   mv <镜像>.signed <镜像>
   ```

3. 缺密钥或没装 `sbsign`（`sbsigntools`）时打印 `NOTE:`/`WARNING:` 后跳过，**不会让安装失败**。

前提：公钥要先注册进 MOK（一次即可）：`sudo mokutil --import /etc/pki/akmods/certs/public_key.der`。
如果密钥是在装完内核之后才生成的，按上面命令手动补签一次即可。

树内模块仍由内核自己的 `MODULE_SIG_ALL`，用构建时生成的一次性密钥签名，与此无关。

### 5.6 为什么不出 `kernel-headers`

Fedora 官方 `kernel-headers`（glibc 用的用户空间 ABI 基线）已占用 `/usr/include/linux`、
`/usr/include/asm` 等路径，我们的包再装同一批文件会与它冲突（dnf 报 file conflict，二者只能装一个）；
替换 Fedora 的 headers 又会影响 glibc 等用户空间构建，收益不成比例。外部模块编译用
`kernel-zen-devel` 即可，因此不产出 headers 子包。

## 6. Copr 工程配置

```bash
# 工程已建好：https://copr.fedorainfracloud.org/coprs/binarytree/zen-kernel-fedora/
# 当前设置与方案一致：
#   chroots=fedora-44-x86_64, enable-net=on, follow-fedora-branching=off,
#   module-hotfixes=off, multilib=off, appstream=off, auto-prune=on,
#   isolation/bootstrap=default, delete-after-days=(空), repo-priority=(空)
# 首轮构建跑通后再加 rawhide chroot：
copr-cli modify zen-kernel-fedora --chroot fedora-rawhide-x86_64

- chroot 数量按需增加；**每多一个 chroot 就多一份 1–2 小时的机器时间**。
- `enable-net` 已开：源码（kernel.org tarball）在 rpkg 生成 SRPM 阶段下载。
- GitHub 仓库 secret：`COPR_CLI_CONFIG`（`copr-cli` 配置文件的完整内容）。
- 手动触发一次：

```bash
copr-cli --config ~/.config/copr buildscm \
  --clone-url https://github.com/red-blakTree/zen-kernel-fedora \
  --commit "$(git rev-parse HEAD)" \
  --spec kernel-zen.spec --type git --method rpkg \
  binarytree/zen-kernel-fedora
```

### 6.1 资源预期（实测参照）

- 同类项目单 chroot 构建：`bieszczaders/kernel-cachyos` 一轮 6 个 chroot 约 120–128 分钟；
  `jplie/kernel-lqx` 一轮 13 个 chroot 约 92 分钟。
- Copr 单次构建上限约 5 小时（[copr-devel 讨论](https://lists.fedoraproject.org/archives/list/copr-devel@lists.fedorahosted.org/message/IULC7NUDBUU2XB4O7NH6UGQR6C5INSOB/)），
  1–2 小时的量级有充足余量。

## 7. 落地与验证步骤

1. **本仓库自检**（已执行）：
   - `python3 scripts/sync_upstream.py` 能解析出 `v7.2.4-zen2` 且不产生多余 diff；
   - `config` 与 Arch 官方 `config.x86_64` 的 sha256 一致。
2. **不本地跑构建**：本仓库文件尚未经过任何实际编译验证，**首个 Copr 构建就是第一次真实验证**。
   失败时看构建页面的 `build.log` / `root.log` / `buildsrpm.log`，改完 spec 用
   `force_build` 手动重跑即可（版本宏和 config 都已提交，重跑不需要改文件）。
3. **Copr 首次构建**：按第 6 节创建工程并触发；重点盯 `%prep` 的 `diff -u config .config`
   输出、zen 补丁是否干净应用（0 fuzz）、以及 `%build` 是否 OOM 或逼近 5 小时上限。
4. **装机验证**：`dnf install ./kernel-zen-*.rpm` → 重启 → `uname -r` → `dkms/akmods` 编一个外部模块。
5. **接入自动构建**：推送到 GitHub，配好 `COPR_CLI_CONFIG`，先手动 `force_build` 跑通一次全流程。

> 仅在 Copr 上反复失败、需要缩小范围时，才考虑 `rpmspec -P kernel-zen.spec` 看宏展开或
> 本地 mock 试编；正常情况下不必在本地跑内核构建。

## 8. 风险与回退

| # | 风险 | 影响 | 处理 |
| --- | --- | --- | --- |
| 8.1 | Arch config 与目标内核版本错位 | 新选项取默认值，可能不适合 Fedora | `olddefconfig` 兜底 + 构建日志里的 config diff 人工 review（每次内核升版本做一次） |
| 8.2 | Fedora chroot 工具链不满足内核要求（pahole/BTF、rustc） | `%build` 失败 | BTF 是既有配置，若 `pahole` 太旧则升级 chroot 或临时关 `DEBUG_INFO_BTF`（同时删掉 `vmlinux.h` 那一步）；Rust 默认已关 |
| 8.3 | 上游改 tag/资产命名，或只发 lqx | 同步脚本报错、构建不触发 | 脚本只认 `vX.Y.Z-zenN` 且必须带 `linux-<tag>.patch.zst`，找不到就**报错退出**（不会静默用旧版本） |
| 8.4 | `DEBUG_INFO=y` 让构建逼近 5 小时上限或撑爆磁盘 | 构建超时 | 先看实测耗时；必要时关 `DEBUG_INFO`/`DEBUG_INFO_BTF` 并去掉 `bpftool vmlinux.h` 步骤，或把 `%make_build` 并行度调低换内存 |
| 8.5 | Copr API/网络抖动 | 单次构建失败 | 手动 `force_build` 重跑；版本宏与 config 都已提交，重跑不需要改文件 |
| 8.6 | 安装时未签名（缺 akmods 密钥 / 缺 `sbsign` / 公钥未注册 MOK） | Secure Boot 机器无法启动该内核 | `%posttrans` 会打印 `NOTE:`；按 5.5 手动 `sbsign` 一次并把公钥 `mokutil --import` 进 MOK |
| 8.7 | GitHub API 限流（未认证 60 次/小时） | 同步 job 失败 | 脚本支持 `GITHUB_TOKEN`（workflow 已注入 `secrets.GITHUB_TOKEN`） |

**回退方式**：所有版本信息都在 git 里 —— `git revert` 同步提交即可回到上一个内核版本，
再手动触发一次构建；临时停自动化就把 workflow 里的 `schedule` 注释掉，改为手工执行
`scripts/sync_upstream.py` + `copr-cli buildscm`。

## 9. 后续可选项（本次不做）

- `kernel-headers` 子包（需要 `%package headers` + `make headers_install` 的文件清单）。
- clang/ThinLTO 变体（参照 `kernel-cachyos-lto.spec` 的 `_lto_args`）。
- `_hz_tick`、`_x86_64_lvl` 这类可调宏：`_hz_tick` 已用于 kernel-power（12.1），x86-64 ISA 优化已独立成 v3 包（第 13 节）。
- IMA/Secure Boot 相关 config（CachyOS 会打开 `CONFIG_IMA*`）。
- nvidia-open 随内核一起构建。

## 10. 待清理项

- `mycopr/packages/kernel-zen/`（草稿 spec + Arch config，未提交）已被本仓库取代，
  建议删除以免两处漂移；`kernel-zen.config` 的内容与 `zen-kernel-fedora/config` 完全一致，
  没有保留价值。

## 11. 验证记录

### 首轮构建：Copr build 10975417（成功）

- 触发方式（非 workflow，手工一次性）：

  ```bash
  copr-cli buildscm --nowait \
    --clone-url https://github.com/red-blakTree/zen-kernel-fedora \
    --commit 5a898f3 --spec kernel-zen.spec --type git --method rpkg \
    binarytree/zen-kernel-fedora
  ```

- 构建页：https://copr.fedorainfracloud.org/coprs/build/10975417
- chroot：`fedora-44-x86_64`（当时工程只开了这一个）
- 耗时：**102.3 分钟**（提交到结束），落在第 6.1 节的 1–2 小时预期内
- 产物（`results/binarytree/zen-kernel-fedora/fedora-44-x86_64/`）：
  - `kernel-zen-7.2.4-zen2.fc44.x86_64.rpm`（元包）
  - `kernel-zen-core-7.2.4-zen2.fc44.x86_64.rpm`
  - `kernel-zen-modules-7.2.4-zen2.fc44.x86_64.rpm`
  - `kernel-zen-devel-7.2.4-zen2.fc44.x86_64.rpm`
  - `kernel-zen-devel-matched-7.2.4-zen2.fc44.x86_64.rpm`
  - `kernel-zen-7.2.4-zen2.fc44.src.rpm`
- repodata 里核到的 provide（akmods/dkms 的匹配依据，三处一致）：
  `kernel-core-uname-r` = `kernel-modules-uname-r` = `kernel-devel-uname-r` = `7.2.4-zen2.fc44.x86_64`
- 说明：这轮验证的是「spec + config + 上游 7.2.4-zen2 源码组合」能编过，也顺带确认了
  `%prep` 里 `zstd -dc | patch -p1`、`olddefconfig`、`bpftool vmlinux.h`、`kernel-devel` 文件清单均无问题。

### 下一步

1. 加 rawhide 并在该 chroot 单独验证一次（`-r fedora-rawhide-x86_64`，避免顺带重编 fedora-44）：

   ```bash
   copr-cli modify zen-kernel-fedora --chroot fedora-rawhide-x86_64
   ```

2. 在 GitHub 仓库配置 secret `COPR_CLI_CONFIG`，让每日 workflow 闭环（第 6 节）。
3. 安全：已经出现在聊天记录里的 API token 建议到 https://copr.fedorainfracloud.org/api/ 重新生成，
   并同步更新 `~/.config/copr` 与 GitHub secret。
4. 真机验证成功标准 4/5（重启后 `uname -r`、akmods/dkms 编外部模块）。

## 12. kernel-power（省电向，第二个包）

同一个仓库、同一份上游（zen tag）、同一份 `config`，只多一个 spec：`kernel-power.spec`。
Copr 工程单独一个：`binarytree/linux-power`（chroots 与 zen 相同：fedora-44 + rawhide）。
workflow 改成矩阵，一次同步同时投两个工程。

### 12.1 与 linux-zen 的差异（全部经核实，不是照搬传说）

改动都写在 spec 的 `%prep` 里，且每个符号都确认过在**本内核 config 中确实存在**
（`scripts/config` 写不存在的符号会静默失效，第 5.2 节的 `X86_64_VERSION` 就是教训）：

| 项 | linux-zen | kernel-power | 依据 |
| --- | --- | --- | --- |
| `CONFIG_HZ` | 1000 | **300**（`%global _hz_tick`） | 时钟中断更少；choice 成员 `HZ_100/250/300/1000` 在 config 中都在 |
| 抢占模型 | `CONFIG_PREEMPT=y`（full） | **`CONFIG_PREEMPT_LAZY=y`** | upstream 原文：lazy「类似 full 抢占，但不过度抢占 SCHED_NORMAL 任务，从而拿回一部分 voluntary 的吞吐」= 平衡档；Fedora 同版本内核默认也是 lazy。运行时默认值由 `kernel/sched/core.c` 的 `preempt_dynamic_init()` 按 choice 符号决定，`preempt=` 可覆盖 |

**踩坑记录（重要）**：第一版写成 `-d PREEMPT -e PREEMPT_VOLUNTARY`，构建日志的 `%prep` diff 显示**抢占没有任何变化**——x86 上 `CONFIG_PREEMPT_VOLUNTARY` 的 Kconfig 依赖是 `depends on !ARCH_HAS_PREEMPT_LAZY`，写进去会被 `olddefconfig` 丢弃（这正是第 5.2 节 `X86_64_VERSION` 那类静默失效）。现改为先 `sed` 删掉 `CONFIG_PREEMPT=` 行、再 `scripts/config -e PREEMPT_LAZY`。**判断某项配置是否真的生效，看构建日志里 `%prep` 打出的 `diff -u config .config`，不要只看 scripts/config 的命令行。**
`CONFIG_PCIEASPM_*` **不动**，保持 BIOS 默认：powersave 能省一点电，但部分机型的 PCIe 链路会出
兼容性问题（这也是它不作为内核默认值的原因）；需要时用启动参数 `pcie_aspm=powersave` 单独开。

### 12.2 zen 本来就省电的部分（没有重复设置）

`RCU_LAZY`、`RCU_NOCB_CPU`、`WQ_POWER_EFFICIENT_DEFAULT`、`SATA_MOBILE_LPM_POLICY=3`、
`SND_HDA_POWER_SAVE_DEFAULT=10`、`USB_AUTOSUSPEND_DELAY=2`、`CPU_FREQ_DEFAULT_GOV_SCHEDUTIL`、
`CPU_IDLE_GOV_TEO`、`ENERGY_MODEL`、`LRU_GEN`、`ACPI_CPPC_LIB`、`INTEL_IDLE`、`X86_INTEL_PSTATE`/`X86_AMD_PSTATE`
——这些在 Arch 的 linux-zen config 里已是省电向取值，power 版因此不动它们。

### 12.3 命名与并存

`Release: power%{_zenrel}` ⇒ `_kver = 7.2.4-power2.fc44.x86_64`，与 `kernel-zen` 的
`7.2.4-zen2.fc44.x86_64` 不同名，`/lib/modules/<kver>` 不冲突，两个内核可以同时装、用 grub 选。

### 12.4 诚实的边界

内核配置只是耗电的一环：笔电上 S0ix/固件、`TLP`/`powertop`、屏幕与 WiFi 省电策略、
`intel_pstate`/`amd_pstate` 的 governor 参数影响通常更大。本包只保证「内核这一层是省电取向」，
不承诺具体续航数字；要量化，就在同一台机器上用 `powertop`/`turbostat` 对比 zen 与 power 两个内核。

## 13. v3 架构优化变体（kernel-zen-v3 / kernel-power-v3）

第 9 节把 ISA 优化列为「本次不做」，这一节把它落地：baseline 之外再加两个包，内核用
`-march=x86-64-v3` 编译。四个包互不冲突，可以同时安装。

### 13.1 命名与并存

| 包 | `Release` | `_kver` |
| --- | --- | --- |
| `kernel-zen` | `zen%{_zenrel}` | `7.2.4-zen2.fc44.x86_64` |
| `kernel-zen-v3` | `zen%{_zenrel}.v3` | `7.2.4-zen2.v3.fc44.x86_64` |
| `kernel-power` | `power%{_zenrel}` | `7.2.4-power2.fc44.x86_64` |
| `kernel-power-v3` | `power%{_zenrel}.v3` | `7.2.4-power2.v3.fc44.x86_64` |

`_kver` 不同 ⇒ `/lib/modules/<kver>`、`/boot/vmlinuz-<kver>`、`kernel-*-uname-r` provide 都不
冲突。代价是构建量翻倍（4 包 × chroot），`/boot` 占用也翻倍。

### 13.2 为什么不用 `CONFIG_X86_64_VERSION`（CachyOS 那条路走不通）

CachyOS spec 写的是 `%define _x86_64_lvl 3` + `scripts/config --set-val X86_64_VERSION 3`，
**照抄到 zen-kernel 上是静默 no-op**：`CONFIG_X86_64_VERSION` 不是 mainline 选项，而是
[graysky2/kernel_compiler_patch](https://github.com/graysky2/kernel_compiler_patch) 往
`arch/x86/Kconfig.cpu` 加的一个 Kconfig 项，外加 `arch/x86/Makefile` 里的
`-march=x86-64-v$(CONFIG_X86_64_VERSION)`。CachyOS 的源码/补丁集里有这个 patch，zen-kernel
没有，而 `scripts/config` 对不存在的符号是静默失效的 —— 正是 5.2 节 `X86_64_VERSION` 记的那个坑。

本仓库改用 `KCFLAGS`（零外部补丁）：

```spec
%global _x86_64_lvl  3
%global _kcflags     -march=x86-64-v%{_x86_64_lvl}
...
%make_build EXTRAVERSION=-%{release}.%{_arch} KCFLAGS="%{_kcflags}" all
```

顶层 `Makefile` 的 `KBUILD_CFLAGS += $(KCFLAGS)` 在 `include arch/x86/Makefile` 之后执行，
注入点与 graysky patch 等价。**代价：它只出现在编译命令行里，`.config` diff 看不到**，所以
5.3 节那套「看 config diff 判断是否生效」的验证对 v3 不适用，改看 13.4。

### 13.3 安全性：`-march` 不会让内核用上向量寄存器

`arch/x86/Makefile` 里有 `-mno-sse -mno-mmx -mno-sse2 -mno-3dnow -mno-avx -mno-sse4a`。
在本容器实测（GCC 16.2.1）：

```
$ gcc -mno-sse -mno-mmx -mno-sse2 -mno-3dnow -mno-avx -mno-sse4a -march=x86-64-v3 -Q --help=target
  -msse [disabled]  -mavx [disabled]  -mavx2 [disabled]
  -mbmi [enabled]   -mbmi2 [enabled]  -mmovbe [enabled]  -mpopcnt [enabled]
```

显式的 `-mno-X` 优先于 `-march` 的默认值，与命令行顺序无关：v3 只带来 BMI1/BMI2/MOVBE/
POPCNT/LZCNT 这类整数指令，内核仍不会生成 FP/SIMD 代码。mainline 的 `CONFIG_X86_NATIVE_CPU`
也是在同一个位置追加 `-march=native`，机制相同。

Rust 侧不跟着设 `KRUSTFLAGS`：`KBUILD_RUSTFLAGS` 本来就是 `-Ctarget-cpu=x86-64`，Rust 代码在
内核里占比很小，暂时保持 baseline。

### 13.4 验证

1. 构建日志里出现 `Building with KCFLAGS=-march=x86-64-v3`（spec 里显式 echo）。
2. 装好后 `uname -r` 是 `7.2.4-zen2.v3.fc44.x86_64`（power 档则是 `-power2.v3`）。
3. 指令级抽查，**两个包对比同一个模块**（xfs 在 zen config 里是模块，`CONFIG_XFS_FS=m`）：

   ```bash
   objdump -d /lib/modules/$(uname -r)/kernel/fs/xfs/xfs.ko \
     | grep -cE '\b(popcnt|andn|bzhi|mulx|shlx)\b'
   ```

   非零说明编译时确实带了 v3；baseline 包同一模块应明显更少。只看非零不够，要两个包对比。
4. 性能/功耗不承诺具体数字；要量化就在同一台机器上自己压测。

### 13.5 风险

| # | 风险 | 处理 |
| --- | --- | --- |
| 13.1 | 不支持的 CPU 上 v3 内核无法启动 | 包名与 `uname -r` 都带 `v3`，README 写明门槛；grub 里保留 baseline 内核 |
| 13.2 | 构建量翻倍（4 包 × chroot，单轮数小时机器时间） | Copr 里按需只构建需要的 package |
| 13.3 | v3 的收益是「小但真实」，别期待质变 | 如实说明，不承诺数字；依据见 graysky 的 benchmark |

Copr 侧只需给两个现有工程各加一个 package（首次 `buildscm` 也会自动创建）：

```bash
copr-cli add-package-scm binarytree/zen-kernel-fedora --name kernel-zen-v3 \
  --clone-url https://github.com/red-blakTree/zen-kernel-fedora \
  --spec kernel-zen-v3.spec --type git --method rpkg
copr-cli add-package-scm binarytree/linux-power --name kernel-power-v3 \
  --clone-url https://github.com/red-blakTree/zen-kernel-fedora \
  --spec kernel-power-v3.spec --type git --method rpkg
```
