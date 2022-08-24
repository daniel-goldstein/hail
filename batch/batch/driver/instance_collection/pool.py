import asyncio
import logging
from typing import Optional

import sortedcontainers

from gear import Database
from hailtop import aiotools
from hailtop.utils import periodically_call

from ...batch_configuration import STANDING_WORKER_MAX_IDLE_TIME_MSECS
from ...inst_coll_config import PoolConfig
from ..instance import Instance
from ..resource_manager import CloudResourceManager
from .base import InstanceCollection, InstanceCollectionManager

log = logging.getLogger('pool')


class Pool(InstanceCollection):
    @staticmethod
    async def create(
        app,
        db: Database,  # BORROWED
        inst_coll_manager: InstanceCollectionManager,
        resource_manager: CloudResourceManager,
        machine_name_prefix: str,
        config: PoolConfig,
        task_manager: aiotools.BackgroundTaskManager,
    ) -> 'Pool':
        pool = Pool(app, db, inst_coll_manager, resource_manager, machine_name_prefix, config, task_manager)
        log.info(f'initializing {pool}')

        async for record in db.select_and_fetchall(
            '''
SELECT instances.*, instances_free_cores_mcpu.free_cores_mcpu
FROM instances
INNER JOIN instances_free_cores_mcpu
ON instances.name = instances_free_cores_mcpu.name
WHERE removed = 0 AND inst_coll = %s;
''',
            (pool.name,),
        ):
            pool.add_instance(Instance.from_record(app, pool, record))

        task_manager.ensure_future(pool.control_loop())
        return pool

    def __init__(
        self,
        app,
        db: Database,  # BORROWED
        inst_coll_manager: InstanceCollectionManager,
        resource_manager: CloudResourceManager,
        machine_name_prefix: str,
        config: PoolConfig,
        task_manager: aiotools.BackgroundTaskManager,  # BORROWED
    ):
        super().__init__(
            db,
            inst_coll_manager,
            resource_manager,
            config.cloud,
            config.name,
            machine_name_prefix,
            is_pool=True,
            max_instances=config.max_instances,
            max_live_instances=config.max_live_instances,
            task_manager=task_manager,
        )
        self.app = app
        self.inst_coll_manager = inst_coll_manager

        self.healthy_instances_by_free_cores = sortedcontainers.SortedSet(key=lambda instance: instance.free_cores_mcpu)

        self.worker_type = config.worker_type
        self.worker_cores = config.worker_cores
        self.worker_local_ssd_data_disk = config.worker_local_ssd_data_disk
        self.worker_external_ssd_data_disk_size_gb = config.worker_external_ssd_data_disk_size_gb
        self.enable_standing_worker = config.enable_standing_worker
        self.standing_worker_cores = config.standing_worker_cores
        self.boot_disk_size_gb = config.boot_disk_size_gb
        self.data_disk_size_gb = config.data_disk_size_gb
        self.data_disk_size_standing_gb = config.data_disk_size_standing_gb
        self.preemptible = config.preemptible

        # FIXME: CI needs to submit jobs to a specific region
        # instead of batch making this decicion on behalf of CI
        self._ci_region = self.inst_coll_manager._default_region

    @property
    def local_ssd_data_disk(self) -> bool:
        return self.worker_local_ssd_data_disk

    def _default_location(self) -> str:
        return self.inst_coll_manager.location_monitor.default_location()

    def config(self):
        return {
            'name': self.name,
            'worker_type': self.worker_type,
            'worker_cores': self.worker_cores,
            'boot_disk_size_gb': self.boot_disk_size_gb,
            'worker_local_ssd_data_disk': self.worker_local_ssd_data_disk,
            'worker_external_ssd_data_disk_size_gb': self.worker_external_ssd_data_disk_size_gb,
            'enable_standing_worker': self.enable_standing_worker,
            'standing_worker_cores': self.standing_worker_cores,
            'max_instances': self.max_instances,
            'max_live_instances': self.max_live_instances,
            'preemptible': self.preemptible,
        }

    def configure(self, pool_config: PoolConfig):
        assert self.name == pool_config.name
        assert self.cloud == pool_config.cloud
        assert self.worker_type == pool_config.worker_type

        self.worker_cores = pool_config.worker_cores
        self.worker_local_ssd_data_disk = pool_config.worker_local_ssd_data_disk
        self.worker_external_ssd_data_disk_size_gb = pool_config.worker_external_ssd_data_disk_size_gb
        self.enable_standing_worker = pool_config.enable_standing_worker
        self.standing_worker_cores = pool_config.standing_worker_cores
        self.boot_disk_size_gb = pool_config.boot_disk_size_gb
        self.data_disk_size_gb = pool_config.data_disk_size_gb
        self.data_disk_size_standing_gb = pool_config.data_disk_size_standing_gb
        self.max_instances = pool_config.max_instances
        self.max_live_instances = pool_config.max_live_instances
        self.preemptible = pool_config.preemptible

    def adjust_for_remove_instance(self, instance):
        super().adjust_for_remove_instance(instance)
        if instance in self.healthy_instances_by_free_cores:
            self.healthy_instances_by_free_cores.remove(instance)

    def adjust_for_add_instance(self, instance):
        super().adjust_for_add_instance(instance)
        if instance.state == 'active' and instance.failed_request_count <= 1:
            self.healthy_instances_by_free_cores.add(instance)

    def get_instance(self, user, cores_mcpu) -> Optional[Instance]:
        i = self.healthy_instances_by_free_cores.bisect_key_left(cores_mcpu)
        while i < len(self.healthy_instances_by_free_cores):
            instance = self.healthy_instances_by_free_cores[i]
            assert isinstance(instance, Instance)
            assert cores_mcpu <= instance.free_cores_mcpu
            if user != 'ci' or (user == 'ci' and instance.region == self._ci_region):
                return instance
            i += 1
        return None

    async def create_instance(
        self,
        cores: int,
        data_disk_size_gb: int,
        max_idle_time_msecs: Optional[int] = None,
        location: Optional[str] = None,
        region: Optional[str] = None,
    ):
        machine_type = self.resource_manager.machine_type(cores, self.worker_type, self.worker_local_ssd_data_disk)
        _, _ = await self._create_instance(
            app=self.app,
            cores=cores,
            machine_type=machine_type,
            job_private=False,
            location=location,
            region=region,
            preemptible=self.preemptible,
            max_idle_time_msecs=max_idle_time_msecs,
            local_ssd_data_disk=self.worker_local_ssd_data_disk,
            data_disk_size_gb=data_disk_size_gb,
            boot_disk_size_gb=self.boot_disk_size_gb,
        )

    async def create_instances_from_ready_cores(self, ready_cores_mcpu, region=None):
        n_live_instances = self.n_instances_by_state['pending'] + self.n_instances_by_state['active']

        if region is None:
            live_free_cores_mcpu = self.live_free_cores_mcpu
        else:
            live_free_cores_mcpu = self.live_free_cores_mcpu_by_region[region]

        instances_needed = (ready_cores_mcpu - live_free_cores_mcpu + (self.worker_cores * 1000) - 1) // (
            self.worker_cores * 1000
        )
        instances_needed = min(
            instances_needed,
            self.max_live_instances - n_live_instances,
            self.max_instances - self.n_instances,
            # 20 queries/s; our GCE long-run quota
            300,
            # n * 16 cores / 15s = excess_scheduling_rate/s = 10/s => n ~= 10
            10,
        )

        if instances_needed > 0:
            log.info(f'creating {instances_needed} new instances')
            # parallelism will be bounded by thread pool
            await asyncio.gather(
                *[
                    self.create_instance(
                        cores=self.worker_cores,
                        data_disk_size_gb=self.data_disk_size_gb,
                        region=region,
                    )
                    for _ in range(instances_needed)
                ]
            )

    async def create_instances(self):
        if self.app['frozen']:
            log.info(f'not creating instances for {self}; batch is frozen')
            return

        ready_cores_mcpu_per_user = self.db.select_and_fetchall(
            '''
SELECT user,
  CAST(COALESCE(SUM(ready_cores_mcpu), 0) AS SIGNED) AS ready_cores_mcpu
FROM user_inst_coll_resources
WHERE inst_coll = %s
GROUP BY user;
''',
            (self.name,),
        )

        if ready_cores_mcpu_per_user is None:
            ready_cores_mcpu_per_user = {}
        else:
            ready_cores_mcpu_per_user = {r['user']: r['ready_cores_mcpu'] async for r in ready_cores_mcpu_per_user}

        ready_cores_mcpu = sum(ready_cores_mcpu_per_user.values())

        free_cores_mcpu = sum([worker.free_cores_mcpu for worker in self.healthy_instances_by_free_cores])
        free_cores = free_cores_mcpu / 1000

        log.info(
            f'{self} n_instances {self.n_instances} {self.n_instances_by_state}'
            f' free_cores {free_cores} live_free_cores {self.live_free_cores_mcpu / 1000}'
            f' ready_cores {ready_cores_mcpu / 1000}'
        )

        if ready_cores_mcpu > 0 and free_cores < 500:
            await self.create_instances_from_ready_cores(ready_cores_mcpu)

        ci_ready_cores_mcpu = ready_cores_mcpu_per_user.get('ci', 0)
        if ci_ready_cores_mcpu > 0 and self.live_free_cores_mcpu_by_region[self._ci_region] == 0:
            await self.create_instances_from_ready_cores(ci_ready_cores_mcpu, region=self._ci_region)

        n_live_instances = self.n_instances_by_state['pending'] + self.n_instances_by_state['active']
        if self.enable_standing_worker and n_live_instances == 0 and self.max_instances > 0:
            await self.create_instance(
                cores=self.standing_worker_cores,
                data_disk_size_gb=self.data_disk_size_standing_gb,
                max_idle_time_msecs=STANDING_WORKER_MAX_IDLE_TIME_MSECS,
            )

    async def control_loop(self):
        await periodically_call(15, self.create_instances)

    def __str__(self):
        return f'pool {self.name}'
