#!/bin/bash

set -ex

sudo apt-get update

sudo apt-get install -y \
    apt-transport-https \
    ca-certificates \
    curl \
    gnupg \
    jq \
    lsb-release \
    software-properties-common

# Install Docker

curl --connect-timeout 5 \
     --max-time 10 \
     --retry 5 \
     --retry-max-time 40 \
     --location \
     --fail \
     --silent \
     --show-error \
     https://download.docker.com/linux/ubuntu/gpg | sudo apt-key add -

sudo add-apt-repository \
   "deb [arch=amd64] https://download.docker.com/linux/ubuntu \
   $(lsb_release -cs) \
   stable"

sudo apt-get install -y docker-ce

sudo rm -rf /var/lib/apt/lists/*

[ -f /etc/docker/daemon.json ] || sudo echo "{}" > /etc/docker/daemon.json

# Install Azure CLI
sudo curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash

sudo az login --identity
sudo az acr login --name {{ global.container_registry_name }}

# Install the azure log analytics agent
sudo wget https://github.com/microsoft/OMS-Agent-for-Linux/releases/download/OMSAgent_v1.14.9-0/omsagent-1.14.9-0.universal.x64.sh \
    -O omsagent.sh
# Install packages and dependencies without configuring log analytics workspace
sudo sh omsagent.sh --install

# avoid "unable to get current user home directory: os/user lookup failed"
export HOME=/root

sudo docker pull {{ global.docker_root_image }}

# add docker daemon debug logging
sudo jq '.debug = true' /etc/docker/daemon.json > daemon.json.tmp
sudo mv daemon.json.tmp /etc/docker/daemon.json
