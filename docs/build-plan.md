# 在 Copr 上编译 zen-kernel 的方案

本文是 `zen-kernel-fedora` 仓库的设计说明：目标、参考实现分析、关键取舍、spec 逐段要点、
Copr 工程配置、验证流程与风险。落地文件见仓库根目录。

## 1. 目标与成功标准

**目标**：在 Copr 上持续产出可安装的 `kernel-zen` RPM（x86_64、Fedora 当前稳定版 + rawhide），
上游 zen-kernel 发布新版本后能自动跟进。

**成功标准**（按顺序验证，全部可独立复核）：

1. `rpmspec -P kernel-zen.spec` 宏展开无报错。
2. Copr 首次构建成功，产出 `kernel-zen-7.2.4-zen2.fcXX.x86_64.rpm` 与
   `-core` / `-modules` / `-devel` / `-devel-matched` 子包。
3. `rpm -qp --provides kernel-zen-core-*.rpm` 含 `kernel-core-uname-r = 7.2.4-zen2.fcXX.x86_64`，
   且与构建日志里 `make kernelrelease` 的结果一致。
4. 安装并重启后 `uname -r` 等于上述 `_kver`；`journalctl -k | head` 无模块签名/依赖类错误。
5. `kernel-zen-devel-matched` 能支撑外部模块构建（akmods/dkms 实测通过）。
6. 上游出新 tag 后，workflow 自动改 spec 宏 + `config` 并触发 Copr 构建。

**明确的非目标**：Secure Boot 签名、`kernel-headers`、`kernel-debuginfo`、LTO/clang 变体、
RT/lqx 变体、多架构。

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
| ISA 等级 | `scripts/config --set-val X86_64_VERSION` | 删除 | 实测该内核 config 里**不存在** `CONFIG_X86_64_VERSION`（`grep` 结果为 0 命中），原写法是无效设置。需要 ISA 优化时应在 `%build` 用 `KCFLAGS` 传 `-march=x86-64-v3` |
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

### 5.4 Rust 开关

Arch config `CONFIG_RUST=y`，且是在 rustc 1.98 / LLVM 22 下生成的。Fedora chroot 的
rustc 未必满足内核 `scripts/min-tool-version.sh` 的最低版本要求，失败点又偏晚，
因此第一版**默认关闭**（`%global _build_rust 0`，在 `%prep` 里 `scripts/config -d RUST`）。
要启用：把宏改成 1（会带上 `BuildRequires: rust rust-src bindgen`），并先在目标 chroot 里
实测 `make LLVM=1 rustavailable`。代价是失去 Rust 驱动（如 nova）。

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
| 8.6 | 内核未签名 | Secure Boot 机器无法启动该内核 | 文档已注明；需要时走用户自签（`sbsign` + 自建 MOK），不在本方案范围 |
| 8.7 | GitHub API 限流（未认证 60 次/小时） | 同步 job 失败 | 脚本支持 `GITHUB_TOKEN`（workflow 已注入 `secrets.GITHUB_TOKEN`） |

**回退方式**：所有版本信息都在 git 里 —— `git revert` 同步提交即可回到上一个内核版本，
再手动触发一次构建；临时停自动化就把 workflow 里的 `schedule` 注释掉，改为手工执行
`scripts/sync_upstream.py` + `copr-cli buildscm`。

## 9. 后续可选项（本次不做）

- `kernel-headers` 子包（需要 `%package headers` + `make headers_install` 的文件清单）。
- clang/ThinLTO 变体（参照 `kernel-cachyos-lto.spec` 的 `_lto_args`）。
- `_hz_tick`、`_x86_64_lvl` 这类可调宏（CachyOS 有，本方案按「先跑通」原则省掉）。
- IMA/Secure Boot 相关 config（CachyOS 会打开 `CONFIG_IMA*`）。
- nvidia-open 随内核一起构建。

## 10. 待清理项

- `mycopr/packages/kernel-zen/`（草稿 spec + Arch config，未提交）已被本仓库取代，
  建议删除以免两处漂移；`kernel-zen.config` 的内容与 `zen-kernel-fedora/config` 完全一致，
  没有保留价值。
