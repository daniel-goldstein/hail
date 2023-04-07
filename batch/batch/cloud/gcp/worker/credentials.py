import base64
import json
from typing import Dict

from ....worker.credentials import CloudUserCredentials


class GCPUserCredentials(CloudUserCredentials):
    def __init__(self, data: Dict[str, str]):
        self._data = data
        self._key = base64.b64decode(self._data['key.json']).decode()
        self._key_dict = json.loads(self._key)

    @property
    def cloud_env_name(self) -> str:
        return 'GOOGLE_APPLICATION_CREDENTIALS'

    @property
    def identity(self) -> str:
        return self._key_dict['client_email']

    @property
    def mount_path(self):
        return '/gsa-key/key.json'

    @property
    def key(self):
        return self._key
