import logging
from functools import wraps

from aiohttp import web

from ..utils import authorization_token
from .instance_collection import InstanceCollectionManager


def instance_from_request(request):
    instance_name = instance_name_from_request(request)
    inst_coll_manager: InstanceCollectionManager = request.app['driver'].inst_coll_manager
    return inst_coll_manager.get_instance(instance_name)


def instance_name_from_request(request) -> str:
    instance_name = request.headers.get('X-Hail-Instance-Name')
    if instance_name is None:
        raise ValueError(f'request is missing required header X-Hail-Instance-Name: {request}')
    return instance_name


def active_instances_only(fun):
    log = logging.getLogger('batch')

    @wraps(fun)
    async def wrapped(request):
        instance = instance_from_request(request)
        if not instance:
            instance_name = instance_name_from_request(request)
            log.info(f'instance not found {instance_name}')
            raise web.HTTPUnauthorized()

        if instance.state != 'active':
            log.info(f'instance not active {instance.name}')
            raise web.HTTPUnauthorized()

        token = authorization_token(request)
        if not token:
            log.info(f'token not found for instance {instance.name}')
            raise web.HTTPUnauthorized()

        inst_coll_manager: InstanceCollectionManager = request.app['driver'].inst_coll_manager
        retrieved_token: str = await inst_coll_manager.name_token_cache.lookup(instance.name)
        if token != retrieved_token:
            log.info('authorization token does not match')
            raise web.HTTPUnauthorized()

        await instance.mark_healthy()

        return await fun(request, instance)

    return wrapped


def activating_instances_only(fun):
    log = logging.getLogger('batch')

    @wraps(fun)
    async def wrapped(request):
        instance = instance_from_request(request)
        if not instance:
            instance_name = instance_name_from_request(request)
            log.info(f'instance {instance_name} not found')
            raise web.HTTPUnauthorized()

        if instance.state != 'pending':
            log.info(f'instance {instance.name} not pending')
            raise web.HTTPUnauthorized()

        activation_token = authorization_token(request)
        if not activation_token:
            log.info(f'activation token not found for instance {instance.name}')
            raise web.HTTPUnauthorized()

        db = request.app['db']
        record = await db.select_and_fetchone(
            'SELECT state FROM instances WHERE name = %s AND activation_token = %s;', (instance.name, activation_token)
        )
        if not record:
            log.info(f'instance {instance.name}, activation token not found in database')
            raise web.HTTPUnauthorized()

        resp = await fun(request, instance)

        return resp

    return wrapped
