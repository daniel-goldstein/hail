from typing import List, Optional, Dict, Any, cast
from typing_extensions import Literal
from mypy_extensions import TypedDict
import re
from collections import defaultdict

from ...instance_config import InstanceConfig, is_power_two

GCP_INSTANCE_CONFIG_VERSION = 4

MACHINE_TYPE_REGEX = re.compile(
    'projects/(?P<project>[^/]+)/zones/(?P<zone>[^/]+)/machineTypes/(?P<machine_family>[^-]+)-(?P<machine_type>[^-]+)-(?P<cores>\\d+)'
)
DISK_TYPE_REGEX = re.compile('(projects/(?P<project>[^/]+)/)?zones/(?P<zone>[^/]+)/diskTypes/(?P<disk_type>.+)')


def parse_machine_type_str(name: str) -> Dict[str, str]:
    match = MACHINE_TYPE_REGEX.fullmatch(name)
    if match is None:
        raise ValueError(f'invalid machine type string: {name}')
    return match.groupdict()


def parse_disk_type(name: str) -> Dict[str, str]:
    match = DISK_TYPE_REGEX.fullmatch(name)
    if match is None:
        raise ValueError(f'invalid disk type string: {name}')
    return match.groupdict()


# instance_config spec
#
# cloud: str
# version: int
# name: str
# instance: dict
#   project: str
#   zone: str
#   family: str (n1, n2, c2, e2, n2d, m1, m2)
#   type_: str (standard, highmem, highcpu)
#   cores: int
#   preemptible: bool
# disks: list of Disk
# job-private: bool
# vm_config: Dict[str, Any]


disk_type_strs = {'pd-ssd', 'pd-standard', 'local-ssd'}
DiskType = Literal['pd-ssd', 'pd-standard', 'local-ssd']


def assert_valid_disk_type(disk_type: str) -> DiskType:
    if disk_type in disk_type_strs:
        return cast(DiskType, disk_type)
    raise ValueError(f'invalid disk type: {disk_type}')


class Disk(TypedDict):
    boot: bool
    project: Optional[str]
    zone: Optional[str]
    type: DiskType
    size: int
    image: Optional[str]


class GCPInstanceConfig(InstanceConfig):
    @staticmethod
    def from_vm_config(vm_config: Dict[str, Any], job_private: bool = False) -> 'GCPInstanceConfig':
        instance_info = parse_machine_type_str(vm_config['machineType'])

        preemptible = vm_config['scheduling']['preemptible']

        disks: List[Disk] = []
        for disk_config in vm_config['disks']:
            params = disk_config['initializeParams']
            disk_info = parse_disk_type(params['diskType'])
            disk_type = assert_valid_disk_type(disk_info['disk_type'])

            if disk_type == 'local-ssd':
                disk_size = 375
            else:
                disk_size = int(params['diskSizeGb'])

            disks.append(
                {
                    'boot': disk_config.get('boot', False),
                    'project': disk_info.get('project'),
                    'zone': disk_info['zone'],
                    'type': disk_type,
                    'size': disk_size,
                    'image': params.get('sourceImage', None),
                }
            )

        config = {
            'cloud': 'gcp',
            'version': GCP_INSTANCE_CONFIG_VERSION,
            'name': vm_config['name'],
            'instance': {
                'project': instance_info['project'],
                'zone': instance_info['zone'],
                'family': instance_info['machine_family'],
                'type': instance_info['machine_type'],
                'cores': int(instance_info['cores']),
                'preemptible': preemptible,
            },
            'disks': disks,
            'job-private': job_private,
            'vm_config': vm_config
        }

        return GCPInstanceConfig(config)

    @staticmethod
    def from_instance_properties(boot_disk_size_gb: int, worker_local_ssd_data_disk: bool, worker_pd_ssd_data_disk_size_gb: int,
                                 worker_type: str, worker_cores: int) -> 'GCPInstanceConfig':
        disks: List[Disk] = [
            {
                'boot': True,
                'project': None,
                'zone': None,
                'type': 'pd-ssd',
                'size': boot_disk_size_gb,
                'image': None,
            }
        ]

        typ: DiskType
        if worker_local_ssd_data_disk:
            typ = 'local-ssd'
            size = 375
        else:
            typ = 'pd-ssd'
            size = worker_pd_ssd_data_disk_size_gb

        disks.append({'boot': False, 'project': None, 'zone': None, 'type': typ, 'size': size, 'image': None})

        config = {
            'version': GCP_INSTANCE_CONFIG_VERSION,
            'instance': {
                'project': None,
                'zone': None,
                'family': 'n1',  # FIXME: need to figure out how to handle variable family types
                'type': worker_type,
                'cores': worker_cores,
                'preemptible': True,
            },
            'disks': disks,
            'job-private': False,
        }

        return GCPInstanceConfig(config)

    def __init__(self, config: Dict[str, Any]):
        self.config = config

        self.version = self.config['version']
        assert self.version >= 3

        self.cloud = self.config.get('cloud', 'gcp')
        self.name = self.config.get('name')
        self.vm_config = self.config.get('vm_config')

        instance = self.config['instance']
        self.disks = self.config['disks']

        self.project = instance['project']
        self.zone = instance['zone']
        self.instance_family = instance['family']
        self.instance_type = instance['type']
        self.cores = instance['cores']

        assert len(self.disks) == 2
        boot_disk = self.disks[0]
        assert boot_disk['boot']
        data_disk = self.disks[1]
        assert not data_disk['boot']

        self.local_ssd_data_disk = data_disk['type'] == 'local-ssd'
        self.data_disk_size_gb = data_disk['size']

        self.job_private = self.config['job-private']
        self.preemptible = instance['preemptible']
        self.worker_type = instance['type']

    @property
    def location(self) -> str:
        return self.zone

    @property
    def machine_type(self) -> str:
        return f'{self.instance_family}-{self.instance_type}-{self.cores}'

    def resources(self, cpu_in_mcpu: int, memory_in_bytes: int, storage_in_gib: int) -> List[Dict[str, Any]]:
        assert memory_in_bytes % (1024 * 1024) == 0, memory_in_bytes
        assert isinstance(storage_in_gib, int), storage_in_gib

        resources = []

        preemptible = 'preemptible' if self.preemptible else 'nonpreemptible'
        worker_fraction_in_1024ths = 1024 * cpu_in_mcpu // (self.cores * 1000)

        resources.append({'name': f'compute/{self.instance_family}-{preemptible}/1', 'quantity': cpu_in_mcpu})

        resources.append(
            {'name': f'memory/{self.instance_family}-{preemptible}/1', 'quantity': memory_in_bytes // 1024 // 1024}
        )

        # storage is in units of MiB
        resources.append({'name': 'disk/pd-ssd/1', 'quantity': storage_in_gib * 1024})

        quantities: Dict[str, int] = defaultdict(lambda: 0)
        for disk in self.disks:
            name = f'disk/{disk["type"]}/1'
            # the factors of 1024 cancel between GiB -> MiB and fraction_1024 -> fraction
            disk_size_in_mib = disk['size'] * worker_fraction_in_1024ths
            quantities[name] += disk_size_in_mib

        for name, quantity in quantities.items():
            resources.append({'name': name, 'quantity': quantity})

        resources.append({'name': 'service-fee/1', 'quantity': cpu_in_mcpu})

        if is_power_two(self.cores) and self.cores <= 256:
            resources.append({'name': 'ip-fee/1024/1', 'quantity': worker_fraction_in_1024ths})
        else:
            raise NotImplementedError(self.cores)

        return resources
