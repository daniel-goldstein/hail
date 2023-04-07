import datetime
import os
import tempfile
from typing import Dict

import aiohttp
import dateutil.parser
from aiohttp import web

from gear.time_limited_max_size_cache import TimeLimitedMaxSizeCache
from hailtop import httpx
from hailtop.aiocloud import aiogoogle
from hailtop.aiocloud.aiogoogle.client.iam_client import GenerateAccessTokenResponse
from hailtop.utils import check_exec_output, retry_transient_errors

from ....globals import HTTP_CLIENT_MAX_SIZE
from ....worker.worker_api import CloudWorkerAPI, ContainerRegistryCredentials, MetadataServer
from ..instance_config import GCPSlimInstanceConfig
from .credentials import GCPUserCredentials
from .disk import GCPDisk

USER_TOKEN_LIFETIME_SECONDS = 3600

AccessTokenCache = TimeLimitedMaxSizeCache[str, GenerateAccessTokenResponse]


async def get_instance_metadata(session: httpx.ClientSession, path: str):
    async with await retry_transient_errors(
        session.get,
        f'http://169.254.169.254/computeMetadata/v1{path}',
        headers={'Metadata-Flavor': 'Google'},
        timeout=aiohttp.ClientTimeout(total=60),  # type: ignore
    ) as resp:
        return await resp.text()


class GCPWorkerAPI(CloudWorkerAPI[GCPUserCredentials]):
    nameserver_ip = '169.254.169.254'

    # async because GoogleSession must be created inside a running event loop
    @staticmethod
    async def from_env() -> 'GCPWorkerAPI':
        project = os.environ['PROJECT']
        zone = os.environ['ZONE'].rsplit('/', 1)[1]
        worker_credentials = aiogoogle.GoogleInstanceMetadataCredentials()
        http_session = httpx.ClientSession()
        google_session = aiogoogle.GoogleSession(credentials=worker_credentials, http_session=http_session)
        numeric_project_id = await get_instance_metadata(http_session, '/project/numeric-project-id')
        return GCPWorkerAPI(project, numeric_project_id, zone, worker_credentials, google_session)

    def __init__(
        self,
        project: str,
        numeric_project_id: str,
        zone: str,
        worker_credentials: aiogoogle.GoogleInstanceMetadataCredentials,
        session: aiogoogle.GoogleSession,
    ):
        self.project = project
        self.numeric_project_id = numeric_project_id
        self.zone = zone
        self._google_session = session
        self._compute_client = aiogoogle.GoogleComputeClient(project, session=session)
        self._gcsfuse_credential_files: Dict[str, str] = {}
        self._iam_creds_client = aiogoogle.GoogleIamCredentialsClient(session=session)
        self._worker_credentials = worker_credentials
        self._user_access_token_cache: AccessTokenCache = TimeLimitedMaxSizeCache(
            self._request_user_identity_access_token,
            int((USER_TOKEN_LIFETIME_SECONDS - 600) * 1e9),  # Renew tokens with ten minutes left in their lifetime
            100,
            'user_identity_token_cache',
        )

    def create_disk(self, instance_name: str, disk_name: str, size_in_gb: int, mount_path: str) -> GCPDisk:
        return GCPDisk(
            zone=self.zone,
            project=self.project,
            instance_name=instance_name,
            name=disk_name,
            size_in_gb=size_in_gb,
            mount_path=mount_path,
            compute_client=self._compute_client,
        )

    def get_cloud_async_fs(self) -> aiogoogle.GoogleStorageAsyncFS:
        return aiogoogle.GoogleStorageAsyncFS(session=self._google_session)

    def get_compute_client(self) -> aiogoogle.GoogleComputeClient:
        return self._compute_client

    def user_credentials(self, credentials: Dict[str, str]) -> GCPUserCredentials:
        return GCPUserCredentials(credentials)

    async def _request_user_identity_access_token(self, user_identity) -> GenerateAccessTokenResponse:
        return await self._iam_creds_client.generate_access_token(
            user_identity, lifetime_seconds=USER_TOKEN_LIFETIME_SECONDS
        )

    async def worker_container_registry_credentials(self) -> ContainerRegistryCredentials:
        access_token = await self._worker_credentials.access_token()
        return {'username': 'oauth2accesstoken', 'password': access_token}

    async def user_container_registry_credentials(
        self, credentials: GCPUserCredentials
    ) -> ContainerRegistryCredentials:
        access_token = await self._user_access_token_cache.lookup(credentials.identity)
        return {'username': 'oauth2accesstoken', 'password': access_token['accessToken']}

    def metadata_server(self) -> 'GoogleMetadataServer':
        return GoogleMetadataServer(self.project, self.numeric_project_id, self._user_access_token_cache)

    def instance_config_from_config_dict(self, config_dict: Dict[str, str]) -> GCPSlimInstanceConfig:
        return GCPSlimInstanceConfig.from_dict(config_dict)

    def _write_gcsfuse_credentials(self, credentials: GCPUserCredentials, mount_base_path_data: str) -> str:
        if mount_base_path_data not in self._gcsfuse_credential_files:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', delete=False) as credsfile:
                credsfile.write(credentials.key)
                self._gcsfuse_credential_files[mount_base_path_data] = credsfile.name
        return self._gcsfuse_credential_files[mount_base_path_data]

    async def _mount_cloudfuse(
        self,
        credentials: GCPUserCredentials,
        mount_base_path_data: str,
        mount_base_path_tmp: str,
        config: dict,
    ):  # pylint: disable=unused-argument

        fuse_credentials_path = self._write_gcsfuse_credentials(credentials, mount_base_path_data)

        bucket = config['bucket']
        assert bucket

        options = ['allow_other']
        if config['read_only']:
            options.append('ro')

        try:
            billing_project_flag = ['--billing-project', config["requester_pays_project"]]
        except KeyError:
            billing_project_flag = []

        await check_exec_output(
            '/usr/bin/gcsfuse',
            '-o',
            ','.join(options),
            '--file-mode',
            '770',
            '--dir-mode',
            '770',
            '--implicit-dirs',
            '--key-file',
            fuse_credentials_path,
            *billing_project_flag,
            bucket,
            mount_base_path_data,
        )

    async def unmount_cloudfuse(self, mount_base_path_data: str):
        try:
            await check_exec_output('fusermount', '-u', mount_base_path_data)
        finally:
            os.remove(self._gcsfuse_credential_files[mount_base_path_data])
            del self._gcsfuse_credential_files[mount_base_path_data]

    def __str__(self):
        return f'project={self.project} zone={self.zone}'


class GoogleMetadataServer(MetadataServer):
    def __init__(self, project, numeric_project_id, user_access_token_cache: AccessTokenCache):
        super().__init__()
        self._project = project
        self._numeric_project_id = numeric_project_id
        self._user_access_token_cache = user_access_token_cache

    async def root(self, _):
        return web.Response(text='computeMetadata/\n')

    async def project_id(self, _):
        return web.Response(text=self._project)

    async def numeric_project_id(self, _):
        return web.Response(text=self._numeric_project_id)

    async def service_accounts(self, request: web.Request):
        user_identity = request['user_credentials'].identity
        accounts = f'{user_identity}/\ndefault/\n'
        return web.Response(text=accounts)

    async def user_service_account(self, request: web.Request):
        user_identity = request['user_credentials'].identity
        recursive = request.query.get('recursive')
        if recursive == 'true':
            return web.json_response(
                {
                    'aliases': ['default'],
                    'email': user_identity,
                    'scopes': ['https://www.googleapis.com/auth/cloud-platform'],
                },
            )
        return web.Response(text='aliases\nemail\nidentity\nscopes\ntoken\n')

    async def user_email(self, request: web.Request):
        return web.Response(text=request['user_credentials'].identity)

    async def user_token(self, request: web.Request):
        user_identity = request['user_credentials'].identity
        access_token_response = await self._user_access_token_cache.lookup(user_identity)
        expiration = dateutil.parser.isoparse(access_token_response['expireTime'])
        expires_in = int((expiration - datetime.datetime.now(datetime.timezone.utc)).total_seconds())

        return web.json_response(
            {
                'access_token': access_token_response['accessToken'],
                'expires_in': expires_in,
                'token_type': 'Bearer',
            }
        )

    def create_app(self) -> web.Application:
        @web.middleware
        async def middleware(request, handler):
            user_identity = request['user_credentials'].identity
            if 'gsa' in request.match_info and request.match_info['gsa'] not in ('default', user_identity):
                raise web.HTTPBadRequest()

            response: web.Response = await handler(request)

            response.enable_compression()
            # `gcloud` does not properly respect `charset`, which aiohttp automatically
            # sets so we have to explicitly erase it
            # See https://github.com/googleapis/google-auth-library-python/blob/b935298aaf4ea5867b5778bcbfc42408ba4ec02c/google/auth/compute_engine/_metadata.py#L170
            if 'application/json' in response.headers['Content-Type']:
                response.headers['Content-Type'] = 'application/json'
            response.headers['Metadata-Flavor'] = 'Google'
            response.headers['Server'] = 'Metadata Server for VM'
            response.headers['X-XSS-Protection'] = '0'
            response.headers['X-Frame-Options'] = 'SAMEORIGIN'
            return response

        metadata_app = web.Application(
            client_max_size=HTTP_CLIENT_MAX_SIZE, middlewares=[self.user_identity_from_ip, middleware]
        )
        metadata_app.add_routes(
            [
                web.get('/', self.root),
                web.get('/computeMetadata/v1/project/project-id', self.project_id),
                web.get('/computeMetadata/v1/project/numeric-project-id', self.numeric_project_id),
                web.get('/computeMetadata/v1/instance/service-accounts/', self.service_accounts),
                web.get('/computeMetadata/v1/instance/service-accounts/{gsa}/', self.user_service_account),
                web.get('/computeMetadata/v1/instance/service-accounts/{gsa}/email', self.user_email),
                web.get('/computeMetadata/v1/instance/service-accounts/{gsa}/token', self.user_token),
            ]
        )
        return metadata_app
