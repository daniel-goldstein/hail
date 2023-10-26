import os

import hail as hl

from ..helpers import skip_unless_service_backend, test_timeout, qobtest
from hail.backend.service_backend import ServiceBackend


@qobtest
@skip_unless_service_backend()
def test_tiny_driver_has_tiny_memory():
    try:
        hl.eval(hl.range(1024 * 1024).map(lambda _: hl.range(1024 * 1024)))
    except hl.utils.FatalError as exc:
        assert "HailException: Hail off-heap memory exceeded maximum threshold: limit " in exc.args[0]
    else:
        assert False


@qobtest
@skip_unless_service_backend()
@test_timeout(batch=6 * 60)
def test_big_driver_has_big_memory():
    backend = hl.current_backend()
    assert isinstance(backend, ServiceBackend)
    # A fresh backend is used for every test so this should only affect this method
    backend.driver_cores = 8
    backend.driver_memory = 'highmem'
    t = hl.utils.range_table(100_000_000, 50)
    # The pytest (client-side) worker dies if we try to realize all 100M rows in memory.
    # Instead, we realize the 100M rows in memory on the driver and then take just the first 10M
    # rows back to the client.
    hl.eval(t.aggregate(hl.agg.collect(t.idx), _localize=False)[:10_000_000])


@qobtest
@skip_unless_service_backend()
def test_tiny_worker_has_tiny_memory():
    try:
        t = hl.utils.range_table(2, n_partitions=2).annotate(nd=hl.nd.ones((30_000, 30_000)))
        t = t.annotate(nd_sum=t.nd.sum())
        t.aggregate(hl.agg.sum(t.nd_sum))
    except Exception as exc:
        assert 'HailException: Hail off-heap memory exceeded maximum threshold' in exc.args[0]
    else:
        assert False


@qobtest
@skip_unless_service_backend()
@test_timeout(batch=10 * 60)
def test_big_worker_has_big_memory():
    backend = hl.current_backend()
    assert isinstance(backend, ServiceBackend)
    backend.worker_cores = 8
    backend.worker_memory = 'highmem'
    t = hl.utils.range_table(2, n_partitions=2).annotate(nd=hl.nd.ones((30_000, 30_000)))
    t = t.annotate(nd_sum=t.nd.sum())
    # We only eval the small thing so that we trigger an OOM on the worker
    # but not the driver or client
    hl.eval(t.aggregate(hl.agg.sum(t.nd_sum), _localize=False))


@qobtest
@skip_unless_service_backend()
@test_timeout(batch=24 * 60)
def test_regions():
    backend = hl.current_backend()
    assert isinstance(backend, ServiceBackend)
    # We run a fresh ServiceBackend for each test so this does not need to be reset
    backend.regions = [os.environ['HAIL_BATCH_REGION']]
    hl.utils.range_table(1, 1).to_pandas()
