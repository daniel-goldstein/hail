import base64
import os
import tempfile
from typing import Dict, List

import aiohttp
import orjson

from hailtop import httpx
from hailtop.aiocloud import aiogoogle
from hailtop.auth.auth import IdentityProvider
from hailtop.utils import check_exec_output, retry_transient_errors

from ....worker.worker_api import CloudWorkerAPI, ContainerRegistryCredentials
from ..instance_config import GCPSlimInstanceConfig
from .disk import GCPDisk


class GCPWorkerAPI(CloudWorkerAPI):
    nameserver_ip = '169.254.169.254'

    # async because GoogleSession must be created inside a running event loop
    @staticmethod
    async def from_env() -> 'GCPWorkerAPI':
        project = os.environ['PROJECT']
        zone = os.environ['ZONE'].rsplit('/', 1)[1]
        compute_client = aiogoogle.GoogleComputeClient(project)
        return GCPWorkerAPI(project, zone, compute_client)

    def __init__(self, project: str, zone: str, compute_client: aiogoogle.GoogleComputeClient):
        self.project = project
        self.zone = zone
        self._compute_client = compute_client
        self._gcsfuse_credential_files: Dict[str, str] = {}

    @property
    def cloud_specific_env_vars_for_user_jobs(self) -> List[str]:
        idp_json = orjson.dumps({'idp': IdentityProvider.GOOGLE.value}).decode('utf-8')
        return [
            'GOOGLE_APPLICATION_CREDENTIALS=/gsa-key/key.json',
            f'HAIL_IDENTITY_PROVIDER_JSON={idp_json}',
        ]

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

    async def worker_container_registry_credentials(self, session: httpx.ClientSession) -> ContainerRegistryCredentials:
        token_dict = await retry_transient_errors(
            session.post_read_json,
            'http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token',
            headers={'Metadata-Flavor': 'Google'},
            timeout=aiohttp.ClientTimeout(total=60),  # type: ignore
        )
        access_token = token_dict['access_token']
        return {'username': 'oauth2accesstoken', 'password': access_token}

    async def user_container_registry_credentials(self, credentials: Dict[str, str]) -> ContainerRegistryCredentials:
        key = orjson.loads(base64.b64decode(credentials['key.json']).decode())
        async with aiogoogle.GoogleServiceAccountCredentials(key) as sa_credentials:
            access_token = await sa_credentials.access_token()
        return {'username': 'oauth2accesstoken', 'password': access_token}

    def instance_config_from_config_dict(self, config_dict: Dict[str, str]) -> GCPSlimInstanceConfig:
        return GCPSlimInstanceConfig.from_dict(config_dict)

    def _write_gcsfuse_credentials(self, credentials: Dict[str, str], mount_base_path_data: str) -> str:
        if mount_base_path_data not in self._gcsfuse_credential_files:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', delete=False) as credsfile:
                credsfile.write(base64.b64decode(credentials['key.json']).decode())
                self._gcsfuse_credential_files[mount_base_path_data] = credsfile.name
        return self._gcsfuse_credential_files[mount_base_path_data]

    async def _mount_cloudfuse(
        self,
        credentials: Dict[str, str],
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

    async def close(self):
        await self._compute_client.close()

    def __str__(self):
        return f'project={self.project} zone={self.zone}'
