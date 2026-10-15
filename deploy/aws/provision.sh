#!/bin/bash
# EC2 user-data for Amazon Linux 2023: prepares the host once (swap, Docker, Compose). The app itself is
# rolled out by deploy.sh, run through SSM, so redeploys never need SSH or a new instance.
set -euxo pipefail
exec > >(tee -a /var/log/cairn-provision.log) 2>&1

# 2 GB swap: image builds and graph algorithms can briefly exceed the instance's RAM.
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

dnf install -y docker dnf-automatic
systemctl enable --now docker
# Security updates install themselves.
sed -i 's/^upgrade_type.*/upgrade_type = security/; s/^apply_updates.*/apply_updates = yes/' /etc/dnf/automatic.conf
systemctl enable --now dnf-automatic.timer

mkdir -p /usr/local/lib/docker/cli-plugins
curl -fsSL https://github.com/docker/compose/releases/download/v2.29.7/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
curl -fsSL https://github.com/docker/buildx/releases/download/v0.17.1/buildx-v0.17.1.linux-amd64 \
  -o /usr/local/lib/docker/cli-plugins/docker-buildx
chmod +x /usr/local/lib/docker/cli-plugins/*
docker compose version
docker buildx version

mkdir -p /opt/cairn-data/falkordb /opt/cairn-data/api
touch /var/lib/cairn-provisioned
