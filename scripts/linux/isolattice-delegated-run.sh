#!/bin/sh
# Run a command inside a systemd user scope that delegates a process-free cgroup
# subtree to the Linux namespace backend.
#
#   scripts/linux/isolattice-delegated-run.sh python3 app.py
#
# The command runs in <scope>/supervisor; ISOLATTICE_LINUX_CGROUP_ROOT points at
# <scope>/sandboxes with the delegated cpu, memory and pids controllers enabled.
# systemd removes the scope, and every sandbox cgroup in it, when it exits.
set -eu
if [ "${ISOLATTICE_DELEGATED_STAGE:-}" != inner ]; then
    XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    export XDG_RUNTIME_DIR
    ISOLATTICE_DELEGATED_STAGE=inner
    export ISOLATTICE_DELEGATED_STAGE
    exec systemd-run --user --scope --collect --quiet -p Delegate=yes -- "$0" "$@"
fi
unset ISOLATTICE_DELEGATED_STAGE
scope="/sys/fs/cgroup$(sed -n 's/^0:://p' /proc/self/cgroup)"
mkdir "$scope/supervisor" "$scope/sandboxes"
echo $$ > "$scope/supervisor/cgroup.procs"
enable=""
for controller in $(cat "$scope/cgroup.controllers"); do
    case "$controller" in
        cpu | memory | pids) enable="$enable +$controller" ;;
    esac
done
if [ -n "$enable" ]; then
    echo "$enable" > "$scope/cgroup.subtree_control"
fi
ISOLATTICE_LINUX_CGROUP_ROOT="$scope/sandboxes"
export ISOLATTICE_LINUX_CGROUP_ROOT
exec "$@"