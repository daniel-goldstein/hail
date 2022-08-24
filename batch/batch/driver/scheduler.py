import logging
import random
import sortedcontainers
from typing import Optional

import prometheus_client as pc

from gear import Database
from hailtop import aiotools
from hailtop.utils import (
    AsyncWorkerPool,
    WaitableSharedPool,
    retry_long_running,
    run_if_changed,
    secret_alnum_string,
    time_msecs,
)

from .instance_collection.pool import Pool
from .instance_collection.base import Instance
from ..utils import Box, ExceededSharesCounter
from .job import schedule_job

log = logging.getLogger('batch.scheduler')

SCHEDULING_LOOP_RUNS = pc.Counter(
    'scheduling_loop_runs',
    'Number of scheduling loop executions per pool',
    ['pool_name'],
)


class PoolScheduler:
    def __init__(
        self,
        app,
        pool: Pool,
        async_worker_pool: AsyncWorkerPool,  # BORROWED
    ):
        self.app = app
        self.scheduler_state_changed = self.app['scheduler_state_changed'].subscribe()
        self.db: Database = app['db']
        self.pool: Pool = pool
        self.async_worker_pool = async_worker_pool
        self.exceeded_shares_counter = ExceededSharesCounter()

    async def compute_fair_share(self):
        free_cores_mcpu = sum([worker.free_cores_mcpu for worker in self.pool.healthy_instances_by_free_cores])

        user_running_cores_mcpu = {}
        user_total_cores_mcpu = {}
        result = {}

        pending_users_by_running_cores = sortedcontainers.SortedSet(key=lambda user: user_running_cores_mcpu[user])
        allocating_users_by_total_cores = sortedcontainers.SortedSet(key=lambda user: user_total_cores_mcpu[user])

        records = self.db.execute_and_fetchall(
            '''
SELECT user,
  CAST(COALESCE(SUM(n_ready_jobs), 0) AS SIGNED) AS n_ready_jobs,
  CAST(COALESCE(SUM(ready_cores_mcpu), 0) AS SIGNED) AS ready_cores_mcpu,
  CAST(COALESCE(SUM(n_running_jobs), 0) AS SIGNED) AS n_running_jobs,
  CAST(COALESCE(SUM(running_cores_mcpu), 0) AS SIGNED) AS running_cores_mcpu
FROM user_inst_coll_resources
WHERE inst_coll = %s
GROUP BY user
HAVING n_ready_jobs + n_running_jobs > 0;
''',
            (self.pool.name,),
            "compute_fair_share",
        )

        async for record in records:
            user = record['user']
            user_running_cores_mcpu[user] = record['running_cores_mcpu']
            user_total_cores_mcpu[user] = record['running_cores_mcpu'] + record['ready_cores_mcpu']
            pending_users_by_running_cores.add(user)
            record['allocated_cores_mcpu'] = 0
            result[user] = record

        def allocate_cores(user, mark):
            result[user]['allocated_cores_mcpu'] = int(mark - user_running_cores_mcpu[user] + 0.5)

        mark = 0
        while free_cores_mcpu > 0 and (pending_users_by_running_cores or allocating_users_by_total_cores):
            lowest_running = None
            lowest_total = None

            if pending_users_by_running_cores:
                lowest_running_user = pending_users_by_running_cores[0]
                lowest_running = user_running_cores_mcpu[lowest_running_user]
                if lowest_running == mark:
                    pending_users_by_running_cores.remove(lowest_running_user)
                    allocating_users_by_total_cores.add(lowest_running_user)
                    continue

            if allocating_users_by_total_cores:
                lowest_total_user = allocating_users_by_total_cores[0]
                lowest_total = user_total_cores_mcpu[lowest_total_user]
                if lowest_total == mark:
                    allocating_users_by_total_cores.remove(lowest_total_user)
                    allocate_cores(lowest_total_user, mark)
                    continue

            allocation = min([c for c in [lowest_running, lowest_total] if c is not None])

            n_allocating_users = len(allocating_users_by_total_cores)
            cores_to_allocate = n_allocating_users * (allocation - mark)

            if cores_to_allocate > free_cores_mcpu:
                mark += int(free_cores_mcpu / n_allocating_users + 0.5)
                free_cores_mcpu = 0
                break

            mark = allocation
            free_cores_mcpu -= cores_to_allocate

        for user in allocating_users_by_total_cores:
            allocate_cores(user, mark)

        return result

    async def schedule_loop_body(self):
        if self.app['frozen']:
            log.info(f'not scheduling any jobs for {self.pool}; batch is frozen')
            return True

        start = time_msecs()
        SCHEDULING_LOOP_RUNS.labels(pool_name=self.pool.name).inc()
        n_scheduled = 0

        # TODO We should set a limit on how many machines we draw from
        # here and cycle through them on each scheduling loop.
        # I think this should work out nicely in practice.
        current_mcpu = {
            r['name']: r['free_cores_mcpu']
            async for r in self.db.select_and_fetchall(
                '''
SELECT instances.name, free_cores_mcpu
FROM instances
LEFT JOIN instances_free_cores_mcpu
ON instances.name = instances_free_cores_mcpu.name
WHERE NOT removed;
    ''',
                (),
                'refresh_cores_mcpu',
            )
        }
        for instance_name, free_cores_mcpu in current_mcpu.items():
            instance = self.pool.inst_coll_manager.get_instance(instance_name)
            if instance:
                instance.set_free_cores_mcpu(free_cores_mcpu)

        user_resources = await self.compute_fair_share()

        total = sum(resources['allocated_cores_mcpu'] for resources in user_resources.values())
        if not total:
            should_wait = True
            return should_wait
        user_share = {
            user: max(int(300 * resources['allocated_cores_mcpu'] / total + 0.5), 20)
            for user, resources in user_resources.items()
        }

        async def user_runnable_jobs(user, remaining):
            async for batch in self.db.select_and_fetchall(
                '''
SELECT batches.id, batches_cancelled.id IS NOT NULL AS cancelled, userdata, user, format_version
FROM batches
LEFT JOIN batches_cancelled
       ON batches.id = batches_cancelled.id
WHERE user = %s AND `state` = 'running';
''',
                (user,),
                "user_runnable_jobs__select_running_batches",
            ):
                async for record in self.db.select_and_fetchall(
                    '''
SELECT job_id, spec, cores_mcpu
FROM jobs FORCE INDEX(jobs_batch_id_state_always_run_inst_coll_cancelled)
WHERE batch_id = %s AND state = 'Ready' AND always_run = 1 AND inst_coll = %s
LIMIT %s;
''',
                    (batch['id'], self.pool.name, remaining.value),
                    "user_runnable_jobs__select_ready_always_run_jobs",
                ):
                    record['batch_id'] = batch['id']
                    record['userdata'] = batch['userdata']
                    record['user'] = batch['user']
                    record['format_version'] = batch['format_version']
                    yield record
                if not batch['cancelled']:
                    async for record in self.db.select_and_fetchall(
                        '''
SELECT job_id, spec, cores_mcpu
FROM jobs FORCE INDEX(jobs_batch_id_state_always_run_cancelled)
WHERE batch_id = %s AND state = 'Ready' AND always_run = 0 AND inst_coll = %s AND cancelled = 0
LIMIT %s;
''',
                        (batch['id'], self.pool.name, remaining.value),
                        "user_runnable_jobs__select_ready_jobs_batch_not_cancelled",
                    ):
                        record['batch_id'] = batch['id']
                        record['userdata'] = batch['userdata']
                        record['user'] = batch['user']
                        record['format_version'] = batch['format_version']
                        yield record

        waitable_pool = WaitableSharedPool(self.async_worker_pool)

        should_wait = True
        for user, resources in user_resources.items():
            allocated_cores_mcpu = resources['allocated_cores_mcpu']
            if allocated_cores_mcpu == 0:
                continue

            scheduled_cores_mcpu = 0
            share = user_share[user]

            remaining = Box(share)
            async for record in user_runnable_jobs(user, remaining):
                attempt_id = secret_alnum_string(6)
                record['attempt_id'] = attempt_id

                if scheduled_cores_mcpu + record['cores_mcpu'] > allocated_cores_mcpu:
                    if random.random() > self.exceeded_shares_counter.rate():
                        self.exceeded_shares_counter.push(True)
                        self.scheduler_state_changed.set()
                        break
                    self.exceeded_shares_counter.push(False)

                instance: Optional[Instance] = self.pool.get_instance(user, record['cores_mcpu'])
                if instance:
                    instance.adjust_free_cores_in_memory(-record['cores_mcpu'])
                    scheduled_cores_mcpu += record['cores_mcpu']
                    n_scheduled += 1

                    async def schedule_with_error_handling(app, record, instance):
                        try:
                            await schedule_job(app, record, instance)
                        except Exception:
                            if instance.state == 'active':
                                instance.adjust_free_cores_in_memory(record['cores_mcpu'])

                    await waitable_pool.call(schedule_with_error_handling, self.app, record, instance)

                remaining.value -= 1
                if remaining.value <= 0:
                    should_wait = False
                    break

        await waitable_pool.wait()

        end = time_msecs()

        if n_scheduled > 0:
            log.info(f'schedule: attempted to schedule {n_scheduled} jobs in {end - start}ms for {self.pool}')

        return should_wait

    def ensure_scheduling_loop(self, task_manager: aiotools.BackgroundTaskManager):
        task_manager.ensure_future(
            retry_long_running('schedule_loop', run_if_changed, self.scheduler_state_changed, self.schedule_loop_body)
        )
