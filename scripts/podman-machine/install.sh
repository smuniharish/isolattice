#!/bin/sh
# Install the delegated rootless Podman service into a Podman machine (as root).
# From the host, with this directory as the current directory:
#   podman machine ssh <machine> "sudo sh -s" < install.sh   # see the Podman guide
# The service script and unit are read from ISOLATTICE_SCRIPT and ISOLATTICE_UNIT
# (base64) when piped, or from files next to this script.
set -eu

uid=${ISOLATTICE_UID:-1000}
if [ -n "${ISOLATTICE_SCRIPT:-}" ]; then
    printf %s "$ISOLATTICE_SCRIPT" | base64 -d > /usr/local/libexec/isolattice-rootless-podman.sh
    printf %s "$ISOLATTICE_UNIT" | base64 -d > /etc/systemd/system/isolattice-rootless-podman@.service
else
    here=$(dirname "$0")
    install -D -m 0755 "$here/isolattice-rootless-podman.sh" \
        /usr/local/libexec/isolattice-rootless-podman.sh
    install -m 0644 "$here/isolattice-rootless-podman@.service" /etc/systemd/system/
fi
chmod 0755 /usr/local/libexec/isolattice-rootless-podman.sh
loginctl enable-linger "$(getent passwd "$uid" | cut -d: -f1)"
systemctl daemon-reload
systemctl enable --now "isolattice-rootless-podman@$uid.service"
systemctl restart "isolattice-rootless-podman@$uid.service"
sleep 2
systemctl is-active "isolattice-rootless-podman@$uid.service"
ls -l "/run/user/$uid/podman/isolattice.sock"
