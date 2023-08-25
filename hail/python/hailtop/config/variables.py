from enum import Enum
from typing import Tuple


class ConfigVariable(str, Enum):
    DOMAIN = 'domain'
    GCS_REQUESTER_PAYS_PROJECT = 'gcs_requester_pays/project'
    GCS_REQUESTER_PAYS_BUCKETS = 'gcs_requester_pays/buckets'
    BATCH_BUCKET = 'batch/bucket'
    BATCH_REMOTE_TMPDIR = 'batch/remote_tmpdir'
    BATCH_REGIONS = 'batch/regions'
    BATCH_BILLING_PROJECT = 'batch/billing_project'
    BATCH_BACKEND = 'batch/backend'
    QUERY_BACKEND = 'query/backend'
    QUERY_JAR_URL = 'query/jar_url'
    QUERY_BATCH_DRIVER_CORES = 'query/batch_driver_cores'
    QUERY_BATCH_WORKER_CORES = 'query/batch_worker_cores'
    QUERY_BATCH_DRIVER_MEMORY = 'query/batch_driver_memory'
    QUERY_BATCH_WORKER_MEMORY = 'query/batch_worker_memory'
    QUERY_NAME_PREFIX = 'query/name_prefix'
    QUERY_DISABLE_PROGRESS_BAR = 'query/disable_progress_bar'

    def to_section_option(self) -> Tuple[str, str]:
        if '/' in self.value:
            return tuple(self.value.split('/'))
        return ('global', self.value)
