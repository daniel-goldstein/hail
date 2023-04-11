import base64
import json
import logging
import os
from shlex import quote as shq
from typing import Dict

from gear.cloud_config import get_global_config

from ....batch_configuration import DEFAULT_NAMESPACE, DOCKER_PREFIX, DOCKER_ROOT_IMAGE, INTERNAL_GATEWAY_IP
from ....file_store import FileStore
from ....instance_config import InstanceConfig
from ...resource_utils import unreserved_worker_data_disk_size_gib
from ...utils import ACCEPTABLE_QUERY_JAR_URL_PREFIX
from ..resource_utils import gcp_machine_type_to_worker_type_and_cores

log = logging.getLogger('create_instance')

BATCH_WORKER_IMAGE = os.environ['HAIL_BATCH_WORKER_IMAGE']


log.info(f'BATCH_WORKER_IMAGE {BATCH_WORKER_IMAGE}')
log.info(f'ACCEPTABLE_QUERY_JAR_URL_PREFIX {ACCEPTABLE_QUERY_JAR_URL_PREFIX}')


def create_vm_config(
    file_store: FileStore,
    resource_rates: Dict[str, float],
    zone: str,
    machine_name: str,
    machine_type: str,
    activation_token: str,
    max_idle_time_msecs: int,
    local_ssd_data_disk: bool,
    data_disk_size_gb: int,
    boot_disk_size_gb: int,
    preemptible: bool,
    job_private: bool,
    project: str,
    instance_config: InstanceConfig,
) -> dict:
    _, cores = gcp_machine_type_to_worker_type_and_cores(machine_type)

    region = instance_config.region_for(zone)
    worker_net_id = 1 if preemptible else 0

    if local_ssd_data_disk:
        worker_data_disk = {
            'type': 'SCRATCH',
            'autoDelete': True,
            'interface': 'NVME',
            'initializeParams': {'diskType': f'zones/{zone}/diskTypes/local-ssd'},
        }
        worker_data_disk_name = 'nvme0n1'
    else:
        worker_data_disk = {
            'autoDelete': True,
            'initializeParams': {
                'diskType': f'projects/{project}/zones/{zone}/diskTypes/pd-ssd',
                'diskSizeGb': str(data_disk_size_gb),
            },
        }
        worker_data_disk_name = 'sdb'

    if job_private:
        unreserved_disk_storage_gb = data_disk_size_gb
    else:
        unreserved_disk_storage_gb = unreserved_worker_data_disk_size_gib(data_disk_size_gb, cores)
    assert unreserved_disk_storage_gb >= 0

    make_global_config = ['mkdir /global-config']
    global_config = get_global_config()
    for name, value in global_config.items():
        make_global_config.append(f'echo -n {shq(value)} > /global-config/{name}')
    make_global_config_str = '\n'.join(make_global_config)

    assert instance_config.is_valid_configuration(resource_rates.keys())

    def scheduling() -> dict:
        result = {
            'automaticRestart': False,
            'onHostMaintenance': 'TERMINATE',
            'preemptible': preemptible,
        }

        if preemptible:
            result.update(
                {
                    'provisioningModel': 'SPOT',
                    'instanceTerminationAction': 'DELETE',
                }
            )

        return result

    return {
        'name': machine_name,
        'machineType': f'projects/{project}/zones/{zone}/machineTypes/{machine_type}',
        'labels': {'role': 'batch2-agent', 'namespace': DEFAULT_NAMESPACE},
        'disks': [
            {
                'boot': True,
                'autoDelete': True,
                'initializeParams': {
                    'sourceImage': f'projects/{project}/global/images/batch-worker-dgoldste-2204',
                    'diskType': f'projects/{project}/zones/{zone}/diskTypes/pd-ssd',
                    'diskSizeGb': str(boot_disk_size_gb),
                },
            },
            worker_data_disk,
        ],
        'networkInterfaces': [
            {
                'network': 'global/networks/default',
                'networkTier': 'PREMIUM',
                'accessConfigs': [{'type': 'ONE_TO_ONE_NAT', 'name': 'external-nat'}],
            }
        ],
        'scheduling': scheduling(),
        'serviceAccounts': [
            {
                'email': f'batch2-agent@{project}.iam.gserviceaccount.com',
                'scopes': ['https://www.googleapis.com/auth/cloud-platform'],
            }
        ],
        'metadata': {
            'items': [
                {
                    'key': 'startup-script',
                    'value': '''
#!/bin/bash
set -x

NAME=$(curl -s http://metadata.google.internal/computeMetadata/v1/instance/name -H 'Metadata-Flavor: Google')
ZONE=$(curl -s http://metadata.google.internal/computeMetadata/v1/instance/zone -H 'Metadata-Flavor: Google')

if [ -f "/started" ]; then
    echo "instance $NAME has previously been started"
    while true; do
    gcloud -q compute instances delete $NAME --zone=$ZONE
    sleep 1
    done
    exit
else
    touch /started
fi

curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/attributes/run_script"  >./run.sh

nohup /bin/bash run.sh >run.log 2>&1 &
    ''',
                },
                {
                    'key': 'run_script',
                    'value': rf'''
#!/bin/bash
set -x

# Setup fluentd
touch /worker.log
touch /run.log

sudo tee /etc/google-cloud-ops-agent/config.yaml <<EOF
logging:
  receivers:
    worker_log:
      type: files
      include_paths:
        - /worker.log
  processors:
    parse_json:
      type: parse_json
    move_severity:
      type: modify_fields
      fields:
        jsonPayload."logging.googleapis.com/severity":
          move_from: jsonPayload.severity
    add_instance_and_namespace_fields:
      type: modify_fields
      fields:
        labels.namespace:
          static_value: "$NAMESPACE"
  service:
    pipelines:
      pipeline:
        receivers: [worker_log]
        processors: [parse_json, move_severity, add_instance_and_namespace_fields]
metrics: # Disable the default metrics collection
  processors:
    metrics_filter:
      type: exclude_metrics
      metrics_pattern: ["agent.googleapis.com/*"]
  service:
    pipelines:
      default_pipeline:
        receivers: []
        processors: []
        exporters: []
EOF

sudo service google-cloud-ops-agent restart

WORKER_DATA_DISK_NAME="{worker_data_disk_name}"
UNRESERVED_WORKER_DATA_DISK_SIZE_GB="{unreserved_disk_storage_gb}"
ACCEPTABLE_QUERY_JAR_URL_PREFIX="{ACCEPTABLE_QUERY_JAR_URL_PREFIX}"

# format worker data disk
sudo mkfs.xfs -m reflink=1 -n ftype=1 /dev/$WORKER_DATA_DISK_NAME
sudo mkdir -p /mnt/disks/$WORKER_DATA_DISK_NAME
sudo mount -o prjquota /dev/$WORKER_DATA_DISK_NAME /mnt/disks/$WORKER_DATA_DISK_NAME
sudo chmod a+w /mnt/disks/$WORKER_DATA_DISK_NAME
XFS_DEVICE=$(xfs_info /mnt/disks/$WORKER_DATA_DISK_NAME | head -n 1 | awk '{{ print $1 }}' | awk  'BEGIN {{ FS = "=" }}; {{ print $2 }}')

# reconfigure docker to use local SSD
sudo service docker stop
sudo mv /var/lib/docker /mnt/disks/$WORKER_DATA_DISK_NAME/docker
sudo ln -s /mnt/disks/$WORKER_DATA_DISK_NAME/docker /var/lib/docker
sudo service docker start

# reconfigure /batch and /logs and /gcsfuse to use local SSD
sudo mkdir -p /mnt/disks/$WORKER_DATA_DISK_NAME/batch/
sudo ln -s /mnt/disks/$WORKER_DATA_DISK_NAME/batch /batch

sudo mkdir -p /mnt/disks/$WORKER_DATA_DISK_NAME/logs/
sudo ln -s /mnt/disks/$WORKER_DATA_DISK_NAME/logs /logs

sudo mkdir -p /mnt/disks/$WORKER_DATA_DISK_NAME/cloudfuse/
sudo ln -s /mnt/disks/$WORKER_DATA_DISK_NAME/cloudfuse /cloudfuse

sudo mkdir -p /etc/netns

CORES=$(nproc)
NAMESPACE=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/attributes/namespace")
ACTIVATION_TOKEN=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/attributes/activation_token")
IP_ADDRESS=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/ip")
PROJECT=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/project/project-id")

BATCH_LOGS_STORAGE_URI=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/attributes/batch_logs_storage_uri")
INSTANCE_ID=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/attributes/instance_id")
INSTANCE_CONFIG=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/attributes/instance_config")
MAX_IDLE_TIME_MSECS=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/attributes/max_idle_time_msecs")
REGION=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/attributes/region")

NAME=$(curl -s http://metadata.google.internal/computeMetadata/v1/instance/name -H 'Metadata-Flavor: Google')
ZONE=$(curl -s http://metadata.google.internal/computeMetadata/v1/instance/zone -H 'Metadata-Flavor: Google')

BATCH_WORKER_IMAGE=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/attributes/batch_worker_image")
DOCKER_ROOT_IMAGE=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/attributes/docker_root_image")
DOCKER_PREFIX=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/attributes/docker_prefix")

INTERNAL_GATEWAY_IP=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/attributes/internal_ip")

# The hail subnet is a /64
HAIL_SUBNET=fd00:4325:1304:f630
THIS_MACHINE_SUBNET=$(python3 -c 'import socket; hex_ip = socket.inet_aton("'$IP_ADDRESS'").hex(); print(":".join([hex_ip[:4], hex_ip[4:]]))')
# The subnet spanned by this machine is /96
THIS_MACHINE=$HAIL_SUBNET:$THIS_MACHINE_SUBNET
sysctl -w net.ipv6.conf.all.forwarding=1

# private job network = 172.20.0.0/16
# public job network = 172.21.0.0/16
# [all networks] Rewrite traffic coming from containers to masquerade as the host
iptables --table nat --append POSTROUTING --source 172.20.0.0/15 --jump MASQUERADE

# [public]
# Block public traffic to the metadata server
# iptables --append FORWARD --source 172.21.0.0/16 --destination 169.254.169.254 --jump DROP
# But allow the internal gateway
# iptables --append FORWARD --destination $INTERNAL_GATEWAY_IP --jump ACCEPT
# And this worker
# iptables --append FORWARD --destination $IP_ADDRESS --jump ACCEPT
# Forbid outgoing requests to cluster-internal IP addresses
INTERNET_INTERFACE=$(ip link list | grep ens | awk -F": " '{{ print $2 }}')
iptables --append FORWARD --jump ACCEPT

WIREGUARD_PORT=46750
ip link add wg0 type wireguard
wg genkey | tee privatekey | wg pubkey > publickey
wg set wg0 private-key privatekey listen-port $WIREGUARD_PORT
rm privatekey

# We set /64 here because this interface will manage all hailnet ipv6 traffic,
# and send those destined for other workers across the network through wireguard
ip -6 addr add $THIS_MACHINE::/64 dev wg0
ip link set up dev wg0

{make_global_config_str}

# retry once
docker pull $BATCH_WORKER_IMAGE || \
(echo 'pull failed, retrying' && sleep 15 && docker pull $BATCH_WORKER_IMAGE)

BATCH_WORKER_IMAGE_ID=$(docker inspect $BATCH_WORKER_IMAGE --format='{{{{.Id}}}}' | cut -d':' -f2)

# So here I go it's my shot.
docker run \
-e CLOUD=gcp \
-e CORES=$CORES \
-e NAME=$NAME \
-e NAMESPACE=$NAMESPACE \
-e ACTIVATION_TOKEN=$ACTIVATION_TOKEN \
-e IP_ADDRESS=$IP_ADDRESS \
-e BATCH_LOGS_STORAGE_URI=$BATCH_LOGS_STORAGE_URI \
-e INSTANCE_ID=$INSTANCE_ID \
-e PROJECT=$PROJECT \
-e ZONE=$ZONE \
-e REGION=$REGION \
-e DOCKER_PREFIX=$DOCKER_PREFIX \
-e DOCKER_ROOT_IMAGE=$DOCKER_ROOT_IMAGE \
-e INSTANCE_CONFIG=$INSTANCE_CONFIG \
-e MAX_IDLE_TIME_MSECS=$MAX_IDLE_TIME_MSECS \
-e BATCH_WORKER_IMAGE=$BATCH_WORKER_IMAGE \
-e BATCH_WORKER_IMAGE_ID=$BATCH_WORKER_IMAGE_ID \
-e INTERNET_INTERFACE=$INTERNET_INTERFACE \
-e UNRESERVED_WORKER_DATA_DISK_SIZE_GB=$UNRESERVED_WORKER_DATA_DISK_SIZE_GB \
-e ACCEPTABLE_QUERY_JAR_URL_PREFIX=$ACCEPTABLE_QUERY_JAR_URL_PREFIX \
-e INTERNAL_GATEWAY_IP=$INTERNAL_GATEWAY_IP \
-e WIREGUARD_PORT=$WIREGUARD_PORT \
-e WIREGUARD_PUBLICKEY=$(cat publickey) \
-e WIREGUARD_IPV6_PREFIX=$THIS_MACHINE \
-v /var/run/docker.sock:/var/run/docker.sock \
-v /var/run/netns:/var/run/netns:shared \
-v /usr/bin/docker:/usr/bin/docker \
-v /usr/sbin/xfs_quota:/usr/sbin/xfs_quota \
-v /batch:/batch:shared \
-v /logs:/logs \
-v /global-config:/global-config \
-v /cloudfuse:/cloudfuse:shared \
-v /etc/netns:/etc/netns \
-v /sys/fs/cgroup:/sys/fs/cgroup \
--mount type=bind,source=/mnt/disks/$WORKER_DATA_DISK_NAME,target=/host \
--mount type=bind,source=/dev,target=/dev,bind-propagation=rshared \
-p 5000:5000 \
--device /dev/fuse \
--device $XFS_DEVICE \
--device /dev \
--privileged \
--cap-add SYS_ADMIN \
--security-opt apparmor:unconfined \
--network host \
--cgroupns host \
$BATCH_WORKER_IMAGE \
python3 -u -m batch.worker.worker >worker.log 2>&1

[ $? -eq 0 ] || tail -n 1000 worker.log

while true; do
gcloud -q compute instances delete $NAME --zone=$ZONE
sleep 1
done
''',
                },
                {
                    'key': 'shutdown-script',
                    'value': '''
set -x

INSTANCE_ID=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/attributes/instance_id")
NAME=$(curl -s http://metadata.google.internal/computeMetadata/v1/instance/name -H 'Metadata-Flavor: Google')

journalctl -u docker.service > dockerd.log
''',
                },
                {'key': 'activation_token', 'value': activation_token},
                {'key': 'batch_worker_image', 'value': BATCH_WORKER_IMAGE},
                {'key': 'docker_root_image', 'value': DOCKER_ROOT_IMAGE},
                {'key': 'docker_prefix', 'value': DOCKER_PREFIX},
                {'key': 'namespace', 'value': DEFAULT_NAMESPACE},
                {'key': 'internal_ip', 'value': INTERNAL_GATEWAY_IP},
                {'key': 'batch_logs_storage_uri', 'value': file_store.batch_logs_storage_uri},
                {'key': 'instance_id', 'value': file_store.instance_id},
                {'key': 'max_idle_time_msecs', 'value': max_idle_time_msecs},
                {'key': 'region', 'value': region},
                {
                    'key': 'instance_config',
                    'value': base64.b64encode(json.dumps(instance_config.to_dict()).encode()).decode(),
                },
            ]
        },
        'tags': {'items': ["batch2-agent"]},
    }
