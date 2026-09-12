# kernel-power.spec -- 省电向 Linux 内核（基于 linux-zen）for Fedora / Copr
#
# 上游源码      : https://github.com/zen-kernel/zen-kernel（与 kernel-zen 同一 tag）
# 打包骨架参考  : copr-linux-cachyos/sources/kernel-cachyos-bore/kernel-cachyos.spec
# 源码组合参考  : Arch Linux linux-zen (kernel.org 原版 tarball + zen 补丁 + Arch config)
# 省电差异      : 见 docs/build-plan.md 第 12 节（HZ / 抢占模型）
#
# 版本宏 _majver/_basekver/_stablekver/_zenrel 由 scripts/sync_upstream.py 自动维护。

# ---- Fedora 打包基础设置（与 CachyOS spec 保持一致）------------------------
%define __spec_install_post %{__os_install_post}
%define _build_id_links none
%define _default_patch_fuzz 2
%define _disable_source_fetch 0
%define debug_package %{nil}
%define make_build make %{?_smp_mflags}
%undefine __brp_mangle_shebangs
%undefine _auto_set_build_flags
%undefine _include_frame_pointers

# ---- 版本 ------------------------------------------------------------------
# 上游 tag 与 kernel-zen 相同：zen tag 形如 v7.2.4-zen2
%global _majver      7
%global _basekver    7.2
%global _stablekver  4
%global _zenrel      2

%global _tag         v%{_basekver}.%{_stablekver}-zen%{_zenrel}
%global _rpmver      %{version}-%{release}
%global _kver        %{_rpmver}.%{_arch}

# 省电档：调度时钟频率，可选 100 / 250 / 300 / 1000（越低越省电、交互延迟越大）
%global _hz_tick     300

# Rust for Linux：内核 scripts/min-tool-version.sh 要求 rustc >= 1.85.0、bindgen >= 0.71.1；
# Fedora 44 与 rawhide 提供 rustc 1.98.1 / bindgen 0.72.1，已满足，因此默认开启。
# 关闭时把这行改成 0（%prep 会 scripts/config -d RUST）。
%global _build_rust  1

%define _kernel_dir /lib/modules/%{_kver}
%define _devel_dir  %{_usrsrc}/kernels/%{_kver}

Name:           kernel-power
Summary:        Power-saving Linux kernel for Fedora (based on linux-zen)
Version:        %{_basekver}.%{_stablekver}
Release:        power%{_zenrel}%{?dist}
License:        GPL-2.0-only
URL:            https://github.com/zen-kernel/zen-kernel

Requires:       kernel-core-uname-r = %{_kver}
Requires:       kernel-modules-uname-r = %{_kver}
Requires:       kernel-modules-core-uname-r = %{_kver}
Provides:       installonlypkg(kernel)

# Source0: kernel.org 原版源码
Source0:        https://cdn.kernel.org/pub/linux/kernel/v%{_majver}.x/linux-%{_basekver}.%{_stablekver}.tar.xz
# Source1: zen 针对上述原版版本发布的补丁（GitHub release 附件，zstd 压缩）
Source1:        https://github.com/zen-kernel/zen-kernel/releases/download/%{_tag}/linux-%{_tag}.patch.zst
# Source2: Arch Linux linux-zen 官方 config，%prep 中做 Fedora 适配
Source2:        config

BuildRequires:  bc
BuildRequires:  bison
BuildRequires:  dwarves
BuildRequires:  elfutils-devel
BuildRequires:  flex
BuildRequires:  gcc
BuildRequires:  gettext-devel
BuildRequires:  kmod
BuildRequires:  make
BuildRequires:  openssl
BuildRequires:  openssl-devel
BuildRequires:  patch
BuildRequires:  perl-Carp
BuildRequires:  perl-devel
BuildRequires:  perl-generators
BuildRequires:  perl-interpreter
BuildRequires:  python3-devel
BuildRequires:  python3-pyyaml
BuildRequires:  python-srpm-macros
BuildRequires:  xz
BuildRequires:  zstd
%if %{_build_rust}
BuildRequires:  rust
BuildRequires:  rust-src
BuildRequires:  bindgen
%endif

%description
The meta package for %{name}.

The Linux ZEN kernel (https://github.com/zen-kernel/zen-kernel) packaged for
Fedora, configured from the Arch Linux official linux-zen configuration with
Fedora-specific adaptations (SELinux LSM, no hardcoded hostname).

%prep
%setup -q -n linux-%{_basekver}.%{_stablekver}

# zen 补丁以 .zst 压缩发布，rpmbuild 不会自动解压，这里显式解压后应用
echo "Applying zen patch %{_tag}..."
zstd -dc %{SOURCE1} | patch -p1

cp %{SOURCE2} .config

# ---- 在 Arch config 之上做 Fedora 适配 -------------------------------------
# Arch 把默认主机名硬编码为 archlinux
scripts/config -u DEFAULT_HOSTNAME

# Arch 未启用 SELinux，Fedora 默认 enforcing
scripts/config --set-str CONFIG_LSM lockdown,yama,integrity,selinux,bpf,landlock

# ==== 省电调优（相对 linux-zen 的差异；下列符号均已在本内核 config 中核实存在）====
# 1) 调度时钟频率 1000 -> %{_hz_tick}Hz：定时器中断更少，空闲/轻载更省电
case %{_hz_tick} in
    100|250|300|1000)
        scripts/config -d HZ_1000 -e HZ_%{_hz_tick} --set-val HZ %{_hz_tick};;
    *)
        echo "Invalid _hz_tick value, falling back to 300"
        scripts/config -d HZ_1000 -e HZ_300 --set-val HZ 300;;
esac

# 2) 抢占模型默认设为 LAZY：upstream 的说明是「类似 full 抢占，但不过度抢占 SCHED_NORMAL
#    任务，从而拿回一部分 voluntary 带来的吞吐」——正是省电/性能的平衡点，Fedora 同版本
#    内核的默认也是它。
#    注意：x86 上 PREEMPT_VOLUNTARY 的 Kconfig 依赖是 !ARCH_HAS_PREEMPT_LAZY，写进去会被
#    olddefconfig 丢掉（上一轮实测确认：diff 里完全没有抢占变更），所以这里先删掉 PREEMPT 行。
#    PREEMPT_DYNAMIC 仍为 y，启动参数 preempt=none|voluntary|full 可随时切换。
sed -i '/^CONFIG_PREEMPT=/d' .config
scripts/config -e PREEMPT_LAZY

# PCIe ASPM 特意保持 BIOS 默认（PCIEASPM_DEFAULT）：powersave 虽能省一点电，但部分机型
# 的 PCIe 链路会出兼容性问题。需要时可加启动参数 pcie_aspm=powersave。

%if ! %{_build_rust}
# 见文件开头 _build_rust 说明
scripts/config -d RUST
%endif

# 按当前内核的 Kconfig 重新求解（Arch config 与实际内核版本可能短暂错位）
make olddefconfig

# 打印实际差异便于排查（有差异不算失败）
diff -u %{SOURCE2} .config || :

%build
%make_build EXTRAVERSION=-%{release}.%{_arch} all
%make_build -C tools/bpf/bpftool vmlinux.h feature-clang-bpf-co-re=1

%install
echo "Installing the kernel image..."
install -Dm644 "$(%make_build -s image_name)" "%{buildroot}%{_kernel_dir}/vmlinuz"
zstd -19 -T0 < Module.symvers > %{buildroot}%{_kernel_dir}/symvers.zst

echo "Installing kernel modules..."
ZSTD_CLEVEL=19 %make_build INSTALL_MOD_PATH="%{buildroot}" INSTALL_MOD_STRIP=1 DEPMOD=/doesnt/exist modules_install

echo "Installing files for the development package..."
install -Dt %{buildroot}%{_devel_dir} -m644 .config Makefile Module.symvers System.map tools/bpf/bpftool/vmlinux.h
cp .config %{buildroot}%{_kernel_dir}/config
cp System.map %{buildroot}%{_kernel_dir}/System.map
cp --parents `find -type f -name "Makefile*" -o -name "Kconfig*"` %{buildroot}%{_devel_dir}
rm -rf %{buildroot}%{_devel_dir}/scripts
rm -rf %{buildroot}%{_devel_dir}/include
cp -a scripts %{buildroot}%{_devel_dir}
rm -rf %{buildroot}%{_devel_dir}/scripts/tracing
rm -f %{buildroot}%{_devel_dir}/scripts/spdxcheck.py

# 下面这些 cp 是为了与 Fedora 的 kernel-devel 对齐
# Install files that are needed for `make scripts` to succeed
cp -a --parents security/selinux/include/classmap.h %{buildroot}%{_devel_dir}
cp -a --parents security/selinux/include/initial_sid_to_string.h %{buildroot}%{_devel_dir}
cp -a --parents tools/include/tools/be_byteshift.h %{buildroot}%{_devel_dir}
cp -a --parents tools/include/tools/le_byteshift.h %{buildroot}%{_devel_dir}

# Install files that are needed for `make prepare` to succeed -- Generic
cp -a --parents tools/include/linux/compiler* %{buildroot}%{_devel_dir}
cp -a --parents tools/include/linux/types.h %{buildroot}%{_devel_dir}
cp -a --parents tools/build/Build.include %{buildroot}%{_devel_dir}
cp --parents tools/build/fixdep.c %{buildroot}%{_devel_dir}
cp --parents tools/objtool/sync-check.sh %{buildroot}%{_devel_dir}
cp -a --parents tools/bpf/resolve_btfids %{buildroot}%{_devel_dir}

cp --parents security/selinux/include/policycap_names.h %{buildroot}%{_devel_dir}
cp --parents security/selinux/include/policycap.h %{buildroot}%{_devel_dir}

cp -a --parents tools/include/asm %{buildroot}%{_devel_dir}
cp -a --parents tools/include/asm-generic %{buildroot}%{_devel_dir}
cp -a --parents tools/include/linux %{buildroot}%{_devel_dir}
cp -a --parents tools/include/uapi/asm %{buildroot}%{_devel_dir}
cp -a --parents tools/include/uapi/asm-generic %{buildroot}%{_devel_dir}
cp -a --parents tools/include/uapi/linux %{buildroot}%{_devel_dir}
cp -a --parents tools/include/vdso %{buildroot}%{_devel_dir}
cp --parents tools/scripts/utilities.mak %{buildroot}%{_devel_dir}
cp -a --parents tools/lib/subcmd %{buildroot}%{_devel_dir}
cp --parents tools/lib/*.c %{buildroot}%{_devel_dir}
cp --parents tools/objtool/*.[ch] %{buildroot}%{_devel_dir}
cp --parents tools/objtool/Build %{buildroot}%{_devel_dir}
cp --parents tools/objtool/include/objtool/*.h %{buildroot}%{_devel_dir}
cp -a --parents tools/lib/bpf %{buildroot}%{_devel_dir}
cp --parents tools/lib/bpf/Build %{buildroot}%{_devel_dir}

# Misc headers
cp -a --parents arch/x86/include %{buildroot}%{_devel_dir}
cp -a --parents tools/arch/x86/include %{buildroot}%{_devel_dir}
cp -a include %{buildroot}%{_devel_dir}/include
cp -a sound/soc/sof/sof-audio.h %{buildroot}%{_devel_dir}/sound/soc/sof
cp -a tools/objtool/objtool %{buildroot}%{_devel_dir}/tools/objtool/
cp -a tools/objtool/fixdep %{buildroot}%{_devel_dir}/tools/objtool/

# Install files that are needed for `make prepare` to succeed -- for x86_64
cp -a --parents arch/x86/entry/syscalls/syscall_32.tbl %{buildroot}%{_devel_dir}
cp -a --parents arch/x86/entry/syscalls/syscall_64.tbl %{buildroot}%{_devel_dir}
cp -a --parents arch/x86/tools/relocs_32.c %{buildroot}%{_devel_dir}
cp -a --parents arch/x86/tools/relocs_64.c %{buildroot}%{_devel_dir}
cp -a --parents arch/x86/tools/relocs.c %{buildroot}%{_devel_dir}
cp -a --parents arch/x86/tools/relocs_common.c %{buildroot}%{_devel_dir}
cp -a --parents arch/x86/tools/relocs.h %{buildroot}%{_devel_dir}
cp -a --parents arch/x86/purgatory/purgatory.c %{buildroot}%{_devel_dir}
cp -a --parents arch/x86/purgatory/stack.S %{buildroot}%{_devel_dir}
cp -a --parents arch/x86/purgatory/setup-x86_64.S %{buildroot}%{_devel_dir}
cp -a --parents arch/x86/purgatory/entry64.S %{buildroot}%{_devel_dir}
cp -a --parents arch/x86/boot/string.h %{buildroot}%{_devel_dir}
cp -a --parents arch/x86/boot/string.c %{buildroot}%{_devel_dir}
cp -a --parents arch/x86/boot/ctype.h %{buildroot}%{_devel_dir}

cp -a --parents scripts/syscalltbl.sh %{buildroot}%{_devel_dir}
cp -a --parents scripts/syscallhdr.sh %{buildroot}%{_devel_dir}

cp -a --parents tools/arch/x86/include/asm %{buildroot}%{_devel_dir}
cp -a --parents tools/arch/x86/include/uapi/asm %{buildroot}%{_devel_dir}
cp -a --parents tools/objtool/arch/x86/lib %{buildroot}%{_devel_dir}
cp -a --parents tools/arch/x86/lib/ %{buildroot}%{_devel_dir}
cp -a --parents tools/arch/x86/tools/gen-insn-attr-x86.awk %{buildroot}%{_devel_dir}
cp -a --parents tools/objtool/arch/x86/ %{buildroot}%{_devel_dir}

# Final cleanups ala Fedora
echo "Cleaning up development files..."
find %{buildroot}%{_devel_dir}/scripts \( -iname "*.o" -o -iname "*.cmd" \) -exec rm -f {} +
find %{buildroot}%{_devel_dir}/tools \( -iname "*.o" -o -iname "*.cmd" \) -exec rm -f {} +
touch -r %{buildroot}%{_devel_dir}/Makefile \
    %{buildroot}%{_devel_dir}/include/generated/uapi/linux/version.h \
    %{buildroot}%{_devel_dir}/include/config/auto.conf

# 这两个链接由 modules 包拥有，未装 -devel 时是死链接（与 Fedora 行为一致）
rm -rf %{buildroot}%{_kernel_dir}/build
ln -s %{_devel_dir} %{buildroot}%{_kernel_dir}/build
ln -s %{_kernel_dir}/build %{buildroot}%{_kernel_dir}/source

# 用桩 initramfs 占位，避免 /boot 空间不足导致 initramfs 生成失败（Fedora #bz530778）
echo "Creating stub initramfs..."
install -dm755 %{buildroot}/boot
dd if=/dev/zero of=%{buildroot}/boot/initramfs-%{_kver}.img bs=1M count=90

%package core
Summary:        Linux ZEN kernel (vmlinuz and core files)
AutoReq:        no
Conflicts:      xfsprogs < 4.3.0-1
Conflicts:      xorg-x11-drv-vmmouse < 13.0.99
Provides:       kernel = %{_rpmver}
Provides:       kernel-core-uname-r = %{_kver}
Provides:       kernel-uname-r = %{_kver}
Provides:       installonlypkg(kernel)
Requires:       kernel-modules-uname-r = %{_kver}
Requires(pre):  /usr/bin/kernel-install
Requires(pre):  coreutils
Requires(pre):  dracut >= 027
Requires(pre):  systemd >= 203-2
Requires(pre):  ((linux-firmware >= 20150904-56.git6ebf5d57) if linux-firmware)
Requires(preun):systemd >= 200
Recommends:     linux-firmware

%description core
The kernel package contains the Linux kernel (vmlinuz), the core of any
Linux operating system.  The kernel handles the basic functions
of the operating system: memory allocation, process allocation, device
input and output, etc.

%post core
mkdir -p %{_localstatedir}/lib/rpm-state/%{name}
touch %{_localstatedir}/lib/rpm-state/%{name}/installing_core_%{_kver}

%posttrans core
rm -f %{_localstatedir}/lib/rpm-state/%{name}/installing_core_%{_kver}
# ostree 镜像构建（或 ostree 启动的系统）里 /run/ostree-booted 可能不存在，
# 但仍需调用 kernel-install 让 05-rpmostree.install 生成 initramfs。
# 非 ostree 的构建容器里跳过，避免 grub2-probe/grub2-editenv 报错。
_ki_layout=$(grep -rs '^layout=' /etc/kernel/install.conf /etc/kernel/install.conf.d /usr/lib/kernel/install.conf /usr/lib/kernel/install.conf.d 2>/dev/null | tail -1 | cut -d= -f2)
if [ "$_ki_layout" = "ostree" ] || [ -d /run/systemd/system ]; then
    # rpm-ostree 对第三方内核会先跑 dracut 再 depmod，这里先保证 modules.dep 存在
    depmod -a %{_kver}
    /bin/kernel-install add %{_kver} %{_kernel_dir}/vmlinuz || exit $?
    if [[ ! -e "/boot/symvers-%{_kver}.zst" ]]; then
        cp "%{_kernel_dir}/symvers.zst" "/boot/symvers-%{_kver}.zst"
        if command -v restorecon &>/dev/null; then
            restorecon "/boot/symvers-%{_kver}.zst"
        fi
    fi
fi

# ---- 安装时在本机签名（构建环境里没有私钥，所以不在这里签）------------------
# 证书优先用 public_key.pem（你指定的路径），Fedora 的 kmodgenca 默认生成的是 .der，故回退到它。
_sb_cert=""
for _cand in /etc/pki/akmods/certs/public_key.pem /etc/pki/akmods/certs/public_key.der; do
    if [ -f "$_cand" ]; then _sb_cert="$_cand"; break; fi
done
if [ -n "$_sb_cert" ] && [ -f /etc/pki/akmods/private/private_key.priv ]; then
    if command -v sbsign >/dev/null 2>&1; then
        for _img in /boot/vmlinuz-%{_kver} /boot/*/%{_kver}/linux; do
            [ -f "$_img" ] || continue
            echo "Signing $_img with $_sb_cert"
            if sbsign --key /etc/pki/akmods/private/private_key.priv --cert "$_sb_cert" \
                      --output "${_img}.signed" "$_img"; then
                mv "${_img}.signed" "$_img"
            else
                echo "WARNING: sbsign failed for $_img" >&2
                rm -f "${_img}.signed"
            fi
        done
    else
        echo "NOTE: sbsign not found (dnf install sbsigntools), skipping kernel signing"
    fi
else
    echo "NOTE: akmods signing keys not found, skipping kernel signing"
fi

%preun core
/bin/kernel-install remove %{_kver} || exit $?
if [ -x /usr/sbin/weak-modules ]; then
    /usr/sbin/weak-modules --remove-kernel %{_kver} || exit $?
fi

%files core
%license COPYING
%ghost %attr(0600, root, root) /boot/initramfs-%{_kver}.img
%ghost %attr(0644, root, root) /boot/symvers-%{_kver}.zst
%{_kernel_dir}/vmlinuz
%{_kernel_dir}/modules.builtin
%{_kernel_dir}/modules.builtin.modinfo
%{_kernel_dir}/symvers.zst
%{_kernel_dir}/config
%{_kernel_dir}/System.map

%package modules
Summary:        Kernel modules package for %{name}
Provides:       kernel-modules = %{_rpmver}
Provides:       kernel-modules-core = %{_rpmver}
Provides:       kernel-modules-extra = %{_rpmver}
Provides:       kernel-modules-uname-r = %{_kver}
Provides:       kernel-modules-core-uname-r = %{_kver}
Provides:       kernel-modules-extra-uname-r = %{_kver}
Provides:       installonlypkg(kernel-module)
Requires:       kernel-uname-r = %{_kver}

%description modules
This package provides kernel modules for the %{name}-core kernel package.

%post modules
if [ ! -f %{_localstatedir}/lib/rpm-state/%{name}/installing_core_%{_kver} ]; then
    mkdir -p %{_localstatedir}/lib/rpm-state/%{name}
    touch %{_localstatedir}/lib/rpm-state/%{name}/need_to_run_dracut_%{_kver}
fi

%posttrans modules
rm -f %{_localstatedir}/lib/rpm-state/%{name}/need_to_run_dracut_%{_kver}
/sbin/depmod -a %{_kver}
if [ ! -e /run/ostree-booted ]; then
    if [ -f %{_localstatedir}/lib/rpm-state/%{name}/need_to_run_dracut_%{_kver} ]; then
        echo "Running: dracut -f --kver %{_kver}"
        dracut -f --kver "%{_kver}" || exit $?
    fi
fi

%files modules
%dir %{_kernel_dir}
%{_kernel_dir}/modules.order
%{_kernel_dir}/build
%{_kernel_dir}/source
%{_kernel_dir}/kernel

%package devel
Summary:        Development package for building kernel modules to match %{name}
Provides:       kernel-devel = %{_rpmver}
Provides:       kernel-devel-uname-r = %{_kver}
Provides:       installonlypkg(kernel)
AutoReqProv:    no
Requires(pre):  findutils
Requires:       findutils
Requires:       perl-interpreter
Requires:       openssl-devel
Requires:       elfutils-libelf-devel
Requires:       bison
Requires:       flex
Requires:       make
Requires:       gcc

%description devel
This package provides kernel headers and makefiles sufficient to build modules
against %{name}.

%post devel
if [ -f /etc/sysconfig/kernel ]; then
    . /etc/sysconfig/kernel || exit $?
fi
if [ "$HARDLINK" != "no" -a -x /usr/bin/hardlink -a ! -e /run/ostree-booted ]; then
    (cd /usr/src/kernels/%{_kver} &&
    /usr/bin/find . -type f | while read f; do
        hardlink -c /usr/src/kernels/*%{?dist}.*/$f $f > /dev/null
    done;
    )
fi

%files devel
%{_devel_dir}

%package devel-matched
Summary:        Meta package to install matching core and devel packages for %{name}
Provides:       kernel-devel-matched = %{_rpmver}
Requires:       %{name}-core = %{_rpmver}
Requires:       %{name}-devel = %{_rpmver}

%description devel-matched
This meta package is used to install matching core and devel packages for %{name}.

%files devel-matched

%files
