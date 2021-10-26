from typing import Dict, TYPE_CHECKING

from gear.cloud_config import get_global_config

from .gcp.driver.driver import GCPDriver
from .gcp.worker.disk import GCPDisk
from .gcp.worker.credentials import GCPUserCredentials
from .gcp.instance_config import GCPInstanceConfig


if TYPE_CHECKING:
    from ..inst_coll_config import InstanceCollectionConfigs  # pylint: disable=cyclic-import
    from ..worker.disk import CloudDisk  # pylint: disable=cyclic-import
    from ..worker.credentials import CloudUserCredentials  # pylint: disable=cyclic-import
    from ..driver.driver import CloudDriver  # pylint: disable=cyclic-import
    from ..instance_config import InstanceConfig  # pylint: disable=cyclic-import


def get_cloud_disk(instance_name: str, disk_name: str, size_in_gb: int, mount_path: str,
                   instance_config: 'InstanceConfig') -> 'CloudDisk':
    cloud = instance_config.cloud
    assert cloud == 'gcp'
    assert isinstance(instance_config, GCPInstanceConfig)
    disk = GCPDisk(
        zone=instance_config.zone,
        project=instance_config.project,
        instance_name=instance_name,
        name=disk_name,
        size_in_gb=size_in_gb,
        mount_path=mount_path,
    )
    return disk


def get_user_credentials(cloud: str, credentials: Dict[str, bytes]) -> 'CloudUserCredentials':
    assert cloud == 'gcp'
    return GCPUserCredentials(credentials)


async def get_cloud_driver(app, machine_name_prefix: str, namespace: str, inst_coll_configs: 'InstanceCollectionConfigs',
                           credentials_file: str) -> 'CloudDriver':
    cloud = get_global_config()['cloud']
    assert cloud == 'gcp'
    return await GCPDriver.create(app, machine_name_prefix, namespace, inst_coll_configs, credentials_file)
