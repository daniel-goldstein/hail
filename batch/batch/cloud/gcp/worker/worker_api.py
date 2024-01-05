import base64
import os
import tempfile
from typing import Dict, List, Tuple

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
        session = aiogoogle.GoogleSession()
        return GCPWorkerAPI(project, zone, session)

    def __init__(self, project: str, zone: str, session: aiogoogle.GoogleSession):
        self.project = project
        self.zone = zone
        self._google_session = session
        self._compute_client = aiogoogle.GoogleComputeClient(project, session=session)
        self._job_credentials: Dict[Tuple[int, int], aiogoogle.GoogleServiceAccountCredentials] = {}
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

    def get_cloud_async_fs(self) -> aiogoogle.GoogleStorageAsyncFS:
        return aiogoogle.GoogleStorageAsyncFS(session=self._google_session)

    def register_job_credentials(self, job_id: Tuple[int, int], credentials: Dict[str, str]) -> None:
        key = orjson.loads(base64.b64decode(credentials['key.json']).decode())
        self._job_credentials[job_id] = aiogoogle.GoogleServiceAccountCredentials(key)

    async def remove_job_credentials(self, job_id: Tuple[int, int]) -> None:
        await self._job_credentials.pop(job_id).close()

    async def worker_container_registry_credentials(self, session: httpx.ClientSession) -> ContainerRegistryCredentials:
        token_dict = await retry_transient_errors(
            session.post_read_json,
            'http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token',
            headers={'Metadata-Flavor': 'Google'},
            timeout=aiohttp.ClientTimeout(total=60),  # type: ignore
        )
        access_token = token_dict['access_token']
        return {'username': 'oauth2accesstoken', 'password': access_token}

    async def user_container_registry_credentials(self, job_id: Tuple[int, int]) -> ContainerRegistryCredentials:
        access_token = await self._job_credentials[job_id].access_token()
        return {'username': 'oauth2accesstoken', 'password': access_token}

    def instance_config_from_config_dict(self, config_dict: Dict[str, str]) -> GCPSlimInstanceConfig:
        return GCPSlimInstanceConfig.from_dict(config_dict)

    def _write_gcsfuse_credentials(self, job_id: Tuple[int, int], mount_base_path_data: str) -> str:
        if mount_base_path_data not in self._gcsfuse_credential_files:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', delete=False) as credsfile:
                credsfile.write(orjson.dumps(self._job_credentials[job_id].key).decode('utf-8'))
                self._gcsfuse_credential_files[mount_base_path_data] = credsfile.name
        return self._gcsfuse_credential_files[mount_base_path_data]

    async def _mount_cloudfuse(
        self,
        job_id: Tuple[int, int],
        mount_base_path_data: str,
        mount_base_path_tmp: str,
        config: dict,
    ):  # pylint: disable=unused-argument

        fuse_credentials_path = self._write_gcsfuse_credentials(job_id, mount_base_path_data)

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
