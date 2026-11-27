#!/usr/bin/env bash
# Run on a fresh standard GitHub Linux runner. The guest owns its kernel,
# disk, Docker daemon and page cache. The SSH client runs on the outer host.
set -euo pipefail
vm=$RUNNER_TEMP/budget-guest
mkdir -p "$vm" evidence
ssh_opts=(-p 2222 -i "$vm/id" -o IdentitiesOnly=yes -o UserKnownHostsFile="$vm/known" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=3)
guest() { ssh "${ssh_opts[@]}" ubuntu@127.0.0.1 "$@"; }
case "$1" in
prepare)
  sudo apt-get -qq update
  sudo apt-get -qq install --no-install-recommends -y qemu-system-x86 qemu-utils cloud-image-utils
  test -c /dev/kvm
  sudo chmod 666 /dev/kvm
  ssh-keygen -q -t ed25519 -N '' -f "$vm/id"
  cat > "$vm/user-data" <<EOF
#cloud-config
users:
  - default
ssh_authorized_keys:
  - $(cat "$vm/id.pub")
EOF
  echo 'instance-id: budget' > "$vm/meta-data"
  cloud-localds "$vm/seed" "$vm/user-data" "$vm/meta-data"
  base=https://cloud-images.ubuntu.com/noble/current
  img=noble-server-cloudimg-amd64.img
  (cd "$vm" && curl --fail --location --retry 3 --output "$img" "$base/$img" &&
    curl --fail --location --retry 3 "$base/SHA256SUMS" | grep -F "$img" | grep -E " [*]?$img$" | sha256sum -c -)
  qemu-img create -q -f qcow2 -F qcow2 -b "$vm/$img" "$vm/disk" 16G
  qemu-system-x86_64 -daemonize -machine accel=kvm -cpu host -smp 2 -m 2048 -display none \
    -serial "file:$vm/console" -drive "file=$vm/disk,if=virtio,format=qcow2" \
    -drive "file=$vm/seed,if=virtio,format=raw,readonly=on" \
    -nic user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:2222-:22
  for i in {1..150}; do guest true 2>/dev/null && break; sleep 2; done
  guest cloud-init status --wait
  git archive --format=tar.gz HEAD | guest 'mkdir -p src && tar -xz -C src'
  guest 'sudo apt-get -qq update && sudo apt-get -qq install --no-install-recommends -y docker.io && sudo systemctl start docker'
  ;;
run)
  guest "cd src && sudo python3 -u dev/host-budget/stack.py $2" | tee evidence/measurements.log
  ;;
results)
  guest 'sudo journalctl -k --no-pager | grep -Ei "killed process|oom-kill" || true' >evidence/kernel-oom.log || true
  guest 'sudo docker ps -a --format "{{.Names}} {{.Status}}"; free -m; df -h /' >evidence/final.txt || true
  guest 'sudo cat /var/tmp/host-budget/samples.csv' >evidence/samples.csv || true
  cat evidence/final.txt evidence/kernel-oom.log
  ;;
esac
