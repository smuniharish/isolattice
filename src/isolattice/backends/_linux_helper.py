"""Trusted in-namespace launcher for the Linux namespace backend.

Run as ``python -I -S _linux_helper.py CONFIG_FD STATUS_FD`` by the backend only.
It uses the standard library alone because it runs before any sandbox exists.

The launcher enters new user, mount, PID, network, IPC and UTS namespaces, then
forks the PID-namespace init. That child joins the execution's delegated cgroup
leaf, enters a cgroup namespace, builds a read-only root from the configured
host paths, pivots into it, and enters a nested user namespace as uid 65534.
It then sets no_new_privs, applies Landlock, drops every capability, installs
a seccomp filter and execs the workload.

Setup errors are written to STATUS_FD, which is close-on-exec, so EOF without
data tells the backend that exec succeeded. The outer launcher waits for the
init and exits with its status, or 128 plus the signal number.
"""

from __future__ import annotations

import ctypes
import errno
import fcntl
import json
import os
import platform
import signal
import socket
import struct
import sys
from typing import Any

if sys.platform == "linux":
    _NAMESPACES = (
        os.CLONE_NEWUSER
        | os.CLONE_NEWNS
        | os.CLONE_NEWPID
        | os.CLONE_NEWNET
        | os.CLONE_NEWIPC
        | os.CLONE_NEWUTS
    )
    _CLONE_NAMESPACE_BITS = (
        0x00020000,  # CLONE_NEWNS
        0x02000000,  # CLONE_NEWCGROUP
        0x04000000,  # CLONE_NEWUTS
        0x08000000,  # CLONE_NEWIPC
        0x10000000,  # CLONE_NEWUSER
        0x20000000,  # CLONE_NEWPID
        0x40000000,  # CLONE_NEWNET
    )
    _PIVOT_ROOT = {"x86_64": 155, "aarch64": 41}
    _MOUNT_SETATTR = 442
    _LANDLOCK_CREATE_RULESET = 444
    _LANDLOCK_ADD_RULE = 445
    _LANDLOCK_RESTRICT_SELF = 446

    _MS_RDONLY = 1
    _MS_NOSUID = 2
    _MS_NODEV = 4
    _MS_NOEXEC = 8
    _MS_REMOUNT = 32
    _MS_BIND = 4096
    _MS_REC = 16384
    _MS_PRIVATE = 1 << 18
    _MNT_DETACH = 2
    _AT_FDCWD = -100
    _AT_RECURSIVE = 0x8000
    _MOUNT_ATTR_RDONLY = 1
    _MOUNT_ATTR_NOSUID = 2
    _MOUNT_ATTR_NODEV = 4

    _PR_SET_PDEATHSIG = 1
    _PR_CAPBSET_DROP = 24
    _PR_SET_NO_NEW_PRIVS = 38
    _PR_CAP_AMBIENT = 47
    _PR_CAP_AMBIENT_CLEAR_ALL = 4
    _CAPABILITY_VERSION_3 = 0x20080522

    _LL_EXECUTE = 1 << 0
    _LL_WRITE_FILE = 1 << 1
    _LL_READ_FILE = 1 << 2
    _LL_READ_DIR = 1 << 3
    _LL_MAKE_CHAR = 1 << 6
    _LL_MAKE_BLOCK = 1 << 11
    _LL_REFER = 1 << 13
    _LL_TRUNCATE = 1 << 14
    _LL_IOCTL_DEV = 1 << 15
    _LL_RULE_PATH_BENEATH = 1
    _LL_NET_BIND_TCP = 1
    _LL_NET_CONNECT_TCP = 2
    _LL_SCOPE_ABSTRACT_UNIX_SOCKET = 1
    _LL_SCOPE_SIGNAL = 2

    _SCMP_ACT_ALLOW = 0x7FFF0000
    _SCMP_CMP_MASKED_EQ = 7
    _DENIED_SYSCALLS = (
        "unshare",
        "setns",
        "mount",
        "umount2",
        "pivot_root",
        "chroot",
        "mount_setattr",
        "move_mount",
        "open_tree",
        "fsopen",
        "fsmount",
        "fsconfig",
        "fspick",
        "keyctl",
        "add_key",
        "request_key",
        "bpf",
        "perf_event_open",
        "userfaultfd",
        "kexec_load",
        "kexec_file_load",
        "init_module",
        "finit_module",
        "delete_module",
        "io_uring_setup",
        "io_uring_enter",
        "io_uring_register",
        "open_by_handle_at",
        "name_to_handle_at",
        "quotactl",
        "quotactl_fd",
        "swapon",
        "swapoff",
        "reboot",
        "syslog",
        "acct",
        "settimeofday",
        "clock_settime",
        "clock_adjtime",
        "adjtimex",
        "lookup_dcookie",
        "vhangup",
        "iopl",
        "ioperm",
        "uselib",
        "ustat",
        "sysfs",
        "fanotify_init",
    )
    _DEVICES = ("null", "zero", "full", "random", "urandom")
    _DEFAULT_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    _SANDBOX_ID = 65534
    _SETUP_FAILURE = 125

    class _SetupError(Exception):
        pass

    _libc = ctypes.CDLL(None, use_errno=True)
    _libc.syscall.restype = ctypes.c_long
    _libc.mount.argtypes = (
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_ulong,
        ctypes.c_char_p,
    )
    _libc.umount2.argtypes = (ctypes.c_char_p, ctypes.c_int)
    _libc.prctl.argtypes = (
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    )

    def _check(result: int, operation: str) -> int:
        if result < 0:
            code = ctypes.get_errno()
            raise _SetupError(f"{operation} failed: {errno.errorcode.get(code, code)}")
        return result

    def _encode(value: str | None) -> bytes | None:
        return None if value is None else os.fsencode(value)

    def _mount(
        source: str | None, target: str, kind: str | None, flags: int, data: str = ""
    ) -> None:
        _check(
            _libc.mount(
                _encode(source), _encode(target), _encode(kind), flags, data.encode() or None
            ),
            f"mount {target}",
        )

    class _MountAttr(ctypes.Structure):
        _fields_ = (
            ("attr_set", ctypes.c_uint64),
            ("attr_clr", ctypes.c_uint64),
            ("propagation", ctypes.c_uint64),
            ("userns_fd", ctypes.c_uint64),
        )

    def _bind_read_only(source: str, target: str) -> None:
        _mount(source, target, None, _MS_BIND | _MS_REC)
        attributes = _MountAttr(
            _MOUNT_ATTR_RDONLY | _MOUNT_ATTR_NOSUID | _MOUNT_ATTR_NODEV, 0, 0, 0
        )
        _check(
            _libc.syscall(
                _MOUNT_SETATTR,
                ctypes.c_int(_AT_FDCWD),
                ctypes.c_char_p(os.fsencode(target)),
                ctypes.c_uint(_AT_RECURSIVE),
                ctypes.byref(attributes),
                ctypes.c_size_t(ctypes.sizeof(attributes)),
            ),
            f"mount_setattr {target}",
        )

    def _write(path: str, text: str) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CLOEXEC)
        try:
            os.write(descriptor, text.encode())
        finally:
            os.close(descriptor)

    def _map_identity(inside: int, outside: int) -> None:
        _write("/proc/self/setgroups", "deny")
        _write("/proc/self/uid_map", f"{inside} {outside} 1")
        _write("/proc/self/gid_map", f"{inside} {outside} 1")

    def _expose(root: str, path: str) -> None:
        target = root + path
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.islink(path):
            os.symlink(os.readlink(path), target)
        elif os.path.isdir(path):
            os.makedirs(target, exist_ok=True)
            _bind_read_only(path, target)
        else:
            os.close(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_CLOEXEC, 0o444))
            _bind_read_only(path, target)

    def _build_devices(root: str) -> None:
        devices = root + "/dev"
        os.makedirs(devices)
        _mount(
            "tmpfs", devices, "tmpfs", _MS_NOSUID | _MS_NOEXEC, "mode=0755,size=64k,nr_inodes=64"
        )
        for name in _DEVICES:
            target = f"{devices}/{name}"
            os.close(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_CLOEXEC, 0o666))
            _mount(f"/dev/{name}", target, None, _MS_BIND)
        for name, target in (
            ("fd", "/proc/self/fd"),
            ("stdin", "/proc/self/fd/0"),
            ("stdout", "/proc/self/fd/1"),
            ("stderr", "/proc/self/fd/2"),
        ):
            os.symlink(target, f"{devices}/{name}")
        _mount(None, devices, None, _MS_REMOUNT | _MS_BIND | _MS_RDONLY | _MS_NOSUID | _MS_NOEXEC)

    def _loopback_up() -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as handle:
            request = struct.pack("16sH14s", b"lo", 0, b"")
            flags = struct.unpack("16sH14s", fcntl.ioctl(handle, 0x8913, request))[1]
            fcntl.ioctl(handle, 0x8914, struct.pack("16sH14s", b"lo", flags | 0x1, b""))

    def _build_root(config: dict[str, Any]) -> None:
        root = str(config["root"])
        _mount(None, "/", None, _MS_REC | _MS_PRIVATE)
        _mount("tmpfs", root, "tmpfs", _MS_NOSUID | _MS_NODEV, "mode=0755,size=1m,nr_inodes=4096")
        for path in config["read_only_paths"]:
            _expose(root, str(path))
        _build_devices(root)
        os.makedirs(root + "/proc")
        _mount("proc", root + "/proc", "proc", _MS_NOSUID | _MS_NODEV | _MS_NOEXEC)
        os.makedirs(root + "/tmp")
        scratch = config["scratch_bytes"]
        size = f",size={scratch}" if scratch is not None else ""
        inodes = config.get("scratch_inodes")
        if inodes is not None:
            size += f",nr_inodes={inodes}"
        _mount("tmpfs", root + "/tmp", "tmpfs", _MS_NOSUID | _MS_NODEV, f"mode=1777{size}")
        workspace = config["workspace"]
        if workspace is not None:
            os.makedirs(root + "/workspace")
            _mount(str(workspace), root + "/workspace", None, _MS_BIND)
            _mount(None, root + "/workspace", None, _MS_REMOUNT | _MS_BIND | _MS_NOSUID | _MS_NODEV)
        _mount(None, root, None, _MS_REMOUNT | _MS_BIND | _MS_RDONLY | _MS_NOSUID | _MS_NODEV)
        os.chdir(root)
        _check(_libc.syscall(_PIVOT_ROOT[platform.machine()], b".", b"."), "pivot_root")
        _check(_libc.umount2(b".", _MNT_DETACH), "detach the host root")
        os.chdir("/")

    class _RulesetAttr(ctypes.Structure):
        _fields_ = (
            ("handled_access_fs", ctypes.c_uint64),
            ("handled_access_net", ctypes.c_uint64),
            ("scoped", ctypes.c_uint64),
        )

    class _PathBeneath(ctypes.Structure):
        _pack_ = 1
        _fields_ = (("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32))

    def _landlock(abi: int, writable: tuple[str, ...]) -> None:
        handled = (1 << 13) - 1
        if abi >= 2:
            handled |= _LL_REFER
        if abi >= 3:
            handled |= _LL_TRUNCATE
        if abi >= 5:
            handled |= _LL_IOCTL_DEV
        attributes = _RulesetAttr(handled, 0, 0)
        size = 8
        if abi >= 4:
            attributes.handled_access_net = _LL_NET_BIND_TCP | _LL_NET_CONNECT_TCP
            size = 16
        if abi >= 6:
            attributes.scoped = _LL_SCOPE_ABSTRACT_UNIX_SOCKET | _LL_SCOPE_SIGNAL
            size = 24
        ruleset = _check(
            _libc.syscall(
                _LANDLOCK_CREATE_RULESET, ctypes.byref(attributes), ctypes.c_size_t(size), 0
            ),
            "landlock_create_ruleset",
        )
        try:
            read = _LL_EXECUTE | _LL_READ_FILE | _LL_READ_DIR
            device = _LL_READ_FILE | _LL_WRITE_FILE | _LL_READ_DIR
            rules = [("/", read), ("/dev", device)]
            rules.extend((path, handled & ~(_LL_MAKE_CHAR | _LL_MAKE_BLOCK)) for path in writable)
            for path, access in rules:
                descriptor = os.open(path, os.O_PATH | os.O_CLOEXEC)
                try:
                    rule = _PathBeneath(access, descriptor)
                    _check(
                        _libc.syscall(
                            _LANDLOCK_ADD_RULE,
                            ruleset,
                            _LL_RULE_PATH_BENEATH,
                            ctypes.byref(rule),
                            0,
                        ),
                        f"landlock_add_rule {path}",
                    )
                finally:
                    os.close(descriptor)
            _check(_libc.syscall(_LANDLOCK_RESTRICT_SELF, ruleset, 0), "landlock_restrict_self")
        finally:
            os.close(ruleset)

    class _CapHeader(ctypes.Structure):
        _fields_ = (("version", ctypes.c_uint32), ("pid", ctypes.c_int))

    class _CapData(ctypes.Structure):
        _fields_ = (
            ("effective", ctypes.c_uint32),
            ("permitted", ctypes.c_uint32),
            ("inheritable", ctypes.c_uint32),
        )

    def _drop_capabilities() -> None:
        with open("/proc/sys/kernel/cap_last_cap", encoding="ascii") as handle:
            last = int(handle.read())
        for capability in range(last + 1):
            _check(_libc.prctl(_PR_CAPBSET_DROP, capability, 0, 0, 0), "drop bounding capability")
        _check(_libc.prctl(_PR_CAP_AMBIENT, _PR_CAP_AMBIENT_CLEAR_ALL, 0, 0, 0), "clear ambient")
        header = _CapHeader(_CAPABILITY_VERSION_3, 0)
        data = (_CapData * 2)()
        _check(_libc.capset(ctypes.byref(header), data), "capset")

    class _ArgCmp(ctypes.Structure):
        _fields_ = (
            ("arg", ctypes.c_uint),
            ("op", ctypes.c_int),
            ("datum_a", ctypes.c_uint64),
            ("datum_b", ctypes.c_uint64),
        )

    def _seccomp() -> None:
        library = ctypes.CDLL("libseccomp.so.2", use_errno=True)
        library.seccomp_init.restype = ctypes.c_void_p
        library.seccomp_init.argtypes = (ctypes.c_uint32,)
        library.seccomp_syscall_resolve_name.argtypes = (ctypes.c_char_p,)
        library.seccomp_rule_add_array.argtypes = (
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.POINTER(_ArgCmp),
        )
        library.seccomp_load.argtypes = (ctypes.c_void_p,)
        library.seccomp_release.argtypes = (ctypes.c_void_p,)
        context = library.seccomp_init(_SCMP_ACT_ALLOW)
        if not context:
            raise _SetupError("seccomp_init failed")
        try:

            def rule(name: str, error: int, comparison: _ArgCmp | None = None) -> None:
                number = library.seccomp_syscall_resolve_name(name.encode())
                if number < 0:
                    return
                action = 0x00050000 | error
                if comparison is None:
                    result = library.seccomp_rule_add_array(context, action, number, 0, None)
                else:
                    result = library.seccomp_rule_add_array(
                        context, action, number, 1, ctypes.byref(comparison)
                    )
                if result < 0:
                    raise _SetupError(f"seccomp rule for {name} failed: {-result}")

            for name in _DENIED_SYSCALLS:
                rule(name, errno.EPERM)
            for bit in _CLONE_NAMESPACE_BITS:
                rule("clone", errno.EPERM, _ArgCmp(0, _SCMP_CMP_MASKED_EQ, bit, bit))
            # clone3 passes flags by pointer; ENOSYS makes libc fall back to filtered clone.
            rule("clone3", errno.ENOSYS)
            result = library.seccomp_load(context)
            if result < 0:
                raise _SetupError(f"seccomp_load failed: {-result}")
        finally:
            library.seccomp_release(context)

    def _resolve(argv0: str, environment: dict[str, str]) -> str:
        if "/" in argv0:
            return argv0
        for directory in environment.get("PATH", _DEFAULT_PATH).split(":"):
            candidate = os.path.join(directory or ".", argv0)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        raise _SetupError(f"executable {argv0!r} was not found in the sandbox PATH")

    def _init(config: dict[str, Any]) -> None:
        _write(str(config["cgroup"]) + "/cgroup.procs", "0")
        os.unshare(os.CLONE_NEWCGROUP)
        _check(_libc.prctl(_PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0), "set parent-death signal")
        socket.sethostname("isolattice")
        _loopback_up()
        _build_root(config)
        os.unshare(os.CLONE_NEWUSER)
        _map_identity(_SANDBOX_ID, 0)
        os.chdir(str(config["cwd"]))
        _check(_libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0), "set no_new_privs")
        writable = ("/tmp", "/workspace") if config["workspace"] is not None else ("/tmp",)
        _landlock(int(config["landlock_abi"]), writable)
        _drop_capabilities()
        environment = {str(key): str(value) for key, value in config["environment"].items()}
        argv = [str(item) for item in config["argv"]]
        executable = _resolve(argv[0], environment) if argv else ""
        for number in (signal.SIGPIPE, signal.SIGXFSZ, signal.SIGINT, signal.SIGTERM):
            signal.signal(number, signal.SIG_DFL)
        signal.pthread_sigmask(signal.SIG_SETMASK, ())
        _seccomp()
        if config["probe"]:
            os._exit(0)
        try:
            os.execve(executable, argv, environment)
        except OSError as error:
            raise _SetupError(f"exec {argv[0]!r} failed: {type(error).__name__}") from error

    def _fail(status: int, message: str) -> None:
        try:
            os.write(status, message.encode("utf-8", "replace")[:4000])
        finally:
            os._exit(_SETUP_FAILURE)

    def main() -> None:
        config_fd, status = int(sys.argv[1]), int(sys.argv[2])
        os.set_inheritable(status, False)
        try:
            with os.fdopen(config_fd, "rb", closefd=True) as handle:
                config = json.loads(handle.read(1024 * 1024))
            _check(
                _libc.prctl(_PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0), "set parent-death signal"
            )
            if os.getppid() != int(config["parent"]):
                raise _SetupError("the calling process exited before the launcher started")
            if platform.machine() not in _PIVOT_ROOT:
                raise _SetupError(f"unsupported architecture {platform.machine()}")
            uid, gid = os.geteuid(), os.getegid()
            os.unshare(_NAMESPACES)
            _write("/proc/self/setgroups", "deny")
            _write("/proc/self/uid_map", f"0 {uid} 1")
            _write("/proc/self/gid_map", f"0 {gid} 1")
            child = os.fork()
        except (_SetupError, OSError, ValueError, KeyError) as error:
            _fail(status, f"namespace setup failed: {error}")
            return
        if child == 0:
            try:
                _init(config)
            except (_SetupError, OSError, ValueError, KeyError, TypeError) as error:
                _fail(status, f"sandbox setup failed: {error}")
            except BaseException as error:
                _fail(status, f"sandbox setup failed: {type(error).__name__}")
            os._exit(_SETUP_FAILURE)
        os.close(status)
        null = os.open("/dev/null", os.O_RDWR)
        for descriptor in (0, 1, 2):
            os.dup2(null, descriptor)
        os.close(null)
        while True:
            try:
                _, wait_status = os.waitpid(child, 0)
                break
            except InterruptedError:
                continue
        code = os.waitstatus_to_exitcode(wait_status)
        os._exit(code if code >= 0 else 128 - code)

    if __name__ == "__main__":
        main()
else:
    raise ImportError("the Linux launcher runs only on Linux")
