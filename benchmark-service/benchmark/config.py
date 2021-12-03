import os
import datetime
from hailtop.utils import secret_alnum_string

HAIL_BENCHMARK_STORAGE_URI = os.environ['HAIL_BENCHMARK_STORAGE_URI']

INSTANCE_ID = os.environ.get('INSTANCE_ID')
if INSTANCE_ID is None:
    INSTANCE_ID = secret_alnum_string(12)

BENCHMARK_RESULTS_PATH = f'{HAIL_BENCHMARK_STORAGE_URI}/benchmark-test/{INSTANCE_ID}'

START_POINT = os.environ.get('START_POINT')
if START_POINT is None:
    now = datetime.datetime.now()
    start_point = now - datetime.timedelta(days=1)
    START_POINT = start_point.strftime("%Y-%m-%dT%H:%M:%SZ")
