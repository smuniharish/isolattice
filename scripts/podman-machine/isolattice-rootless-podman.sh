#!/bin/sh
# Run a rootless Podman API service for one UID inside a delegated cgroup v2
# subtree. Intended for WSL-based Podman machines, where systemd's cgroup root
# (/non-systemd) contains processes from outside the distribution's PID
# namespace, so systemd can never delegate controllers to user@UID.service.
#
# Usage (as root): isolattice-rootless-podman.sh UID [CONTROLLERS]
# The socket is /run/user/UID/podman/isolattice.sock. Run it from the systemd
# unit isolattice-rootless-podman@.service so it starts at every boot.
set -eu

uid=${1:?usage: isolattice-rootless-podman.sh UID [CONTROLLERS]}
controllers=${2:-"cpu memory pids io"}
case "$uid" in
    '' | *[!0-9]* | 0) echo "UID must be a positive integer" >&2; exit 64 ;;
esac

root=/sys/fs/cgroup
[ "$(stat -fc %T "$root")" = cgroup2fs ] || { echo "cgroup v2 is required" >&2; exit 69; }
user=$(getent passwd "$uid" | cut -d: -f1)
home=$(getent passwd "$uid" | cut -d: -f6)
[ -n "$user" ] || { echo "UID $uid does not exist" >&2; exit 67; }
runtime=/run/user/$uid
[ -d "$runtime" ] || { echo "$runtime is missing; enable lingering for $user" >&2; exit 69; }

enable=""
for controller in $controllers; do
    grep -qw "$controller" "$root/cgroup.controllers" || {
        echo "controller $controller is unavailable at the cgroup root" >&2
        exit 69
    }
    enable="$enable +$controller"
done

# The real root is exempt from the no-internal-process rule; non-root levels
# must stay process-free, so the service itself lives in a leaf.
delegated=$root/isolattice-rootless-$uid
echo "$enable" > "$root/cgroup.subtree_control"
mkdir -p "$delegated/service"
chown "$uid:$uid" "$delegated" "$delegated/cgroup.procs" \
    "$delegated/cgroup.subtree_control" "$delegated/cgroup.threads"
chown -R "$uid:$uid" "$delegated/service"
echo "$enable" > "$delegated/cgroup.subtree_control"
install -d -o "$uid" -g "$uid" -m 0700 "$runtime/podman"

echo $$ > "$delegated/service/cgroup.procs"
cd /
exec setpriv --reuid="$uid" --regid="$uid" --init-groups --inh-caps=-all \
    env -i HOME="$home" USER="$user" LOGNAME="$user" \
    PATH=/usr/local/bin:/usr/bin XDG_RUNTIME_DIR="$runtime" \
    podman system service --time=0 "unix://$runtime/podman/isolattice.sock"
