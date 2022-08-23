import asyncio
import logging
import os
import re

import googlecloudprofiler
import uvloop
from aiohttp import web
from prometheus_async.aio.web import server_stats

from gear import Database, monitor_endpoints_middleware, setup_aiohttp_session
from hailtop import aiotools, httpx
from hailtop.config import get_deploy_config
from hailtop.hail_logging import AccessLogger
from hailtop.utils import periodically_call

from ...globals import HTTP_CLIENT_MAX_SIZE
from ..job import mark_job_complete, mark_job_started
from ..instance import Instance
from ..utils import active_instances_only

uvloop.install()

log = logging.getLogger('batch')

deploy_config = get_deploy_config()

routes = web.RouteTableDef()

HAIL_SHOULD_PROFILE = os.environ.get('HAIL_SHOULD_PROFILE', False)
DEFAULT_NAMESPACE = os.environ['HAIL_DEFAULT_NAMESPACE']
CLOUD = os.environ['CLOUD']
HAIL_SHA = os.environ['HAIL_SHA']


async def notify_driver_open_cores(app):
    client_session: httpx.ClientSession = app['client_session']
    event: asyncio.Event = app['state_changed']
    await event.wait()
    async with client_session.post(
        deploy_config.url('batch-driver', '/api/v1alpha/scheduler-state-changed'),
    ):
        pass


@routes.post('/api/v1alpha/instances/job_started')
@active_instances_only
async def job_started(request, instance: Instance):
    return await asyncio.shield(job_started_1(request, instance))


async def job_started_1(request, instance: Instance):
    body = await request.json()

    job_status = body['status']
    await mark_job_started(
        request.app,
        job_status['batch_id'],
        job_status['job_id'],
        job_status['attempt_id'],
        instance.name,
        job_status['start_time'],
        job_status.get('resources'),
    )

    request.app['state_changed'].set()
    return web.Response()


@routes.post('/api/v1alpha/instances/job_complete')
@active_instances_only
async def job_complete(request, instance: Instance):
    return await asyncio.shield(job_complete_1(request, instance))


async def job_complete_1(request, instance: Instance):
    body = await request.json()

    job_status = body['status']

    state = job_status['state']
    if state == 'succeeded':
        new_state = 'Success'
    elif state == 'error':
        new_state = 'Error'
    else:
        assert state == 'failed', state
        new_state = 'Failed'

    await mark_job_complete(
        request.app,
        job_status['batch_id'],
        job_status['job_id'],
        job_status['attempt_id'],
        instance.name,
        new_state,
        job_status['status'],
        job_status['start_time'],
        job_status['end_time'],
        'completed',
        job_status.get('resources'),
    )

    request.app['state_changed'].set()
    return web.Response()


async def on_startup(app: web.Application):
    db = Database()
    await db.async_init(maxsize=50)
    app['db'] = db
    app['client_session'] = httpx.client_session()

    app['task_manager'] = aiotools.BackgroundTaskManager()
    app['state_changed'] = asyncio.Event()
    app['task_manager'].ensure_future(periodically_call(0.2, notify_driver_open_cores, app))


class BatchDbProxyAccessLogger(AccessLogger):
    def __init__(self, logger: logging.Logger, log_format: str):
        super().__init__(logger, log_format)
        self.exclude = [
            (endpoint[0], re.compile(deploy_config.base_path('batch-db-proxy') + endpoint[1]))
            for endpoint in [
                ('POST', '/api/v1alpha/instances/job_complete'),
                ('POST', '/api/v1alpha/instances/job_started'),
                ('GET', '/metrics'),
            ]
        ]

    def log(self, request, response, time):
        for method, path_expr in self.exclude:
            if path_expr.fullmatch(request.path) and method == request.method:
                return

        super().log(request, response, time)


def run():
    if HAIL_SHOULD_PROFILE and CLOUD == 'gcp':
        profiler_tag = DEFAULT_NAMESPACE
        if profiler_tag == 'default':
            profiler_tag = DEFAULT_NAMESPACE + f'-{HAIL_SHA[0:12]}'
        googlecloudprofiler.start(
            service='batch-db-proxy',
            service_version=profiler_tag,
            # https://cloud.google.com/profiler/docs/profiling-python#agent_logging
            verbose=3,
        )
    app = web.Application(client_max_size=HTTP_CLIENT_MAX_SIZE, middlewares=[monitor_endpoints_middleware])
    setup_aiohttp_session(app)

    app.add_routes(routes)
    app.on_startup.append(on_startup)
    app.router.add_get("/metrics", server_stats)

    web.run_app(
        deploy_config.prefix_application(app, 'batch-db-proxy'),
        host='0.0.0.0',
        port=5000,
        access_log_class=BatchDbProxyAccessLogger,
    )
