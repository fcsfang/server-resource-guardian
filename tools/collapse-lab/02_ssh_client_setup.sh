#!/bin/bash
# authorize the Windows client key for passwordless SSH as the lab user.
# adjust KEY_PATH and LAB_USER for your environment; WSL2 example below.
set -e
LAB_USER=${1:-csfang}
KEY_PATH=${2:-/mnt/c/Users/CHANGE_ME/.ssh/id_ed25519.pub}
install -d -m 700 -o "$LAB_USER" -g "$LAB_USER" "/home/$LAB_USER/.ssh"
cat "$KEY_PATH" >> "/home/$LAB_USER/.ssh/authorized_keys"
sort -u "/home/$LAB_USER/.ssh/authorized_keys" -o "/home/$LAB_USER/.ssh/authorized_keys"
chown "$LAB_USER:$LAB_USER" "/home/$LAB_USER/.ssh/authorized_keys"
chmod 600 "/home/$LAB_USER/.ssh/authorized_keys"
# diagnostics over SSH need docker access (disposable lab only)
usermod -aG docker "$LAB_USER" 2>/dev/null || true
echo "authorized: $LAB_USER (docker group included)"
echo "wsl ip: $(hostname -I | awk '{print $1}')"
