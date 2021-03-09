from functools import wraps
import prometheus_client as pc  # type: ignore
from prometheus_async.aio import time as prom_async_time  # type: ignore
from typing import List

REQUEST_TIME = pc.Summary('http_request_latency_seconds', 'Endpoint latency in seconds', ['endpoint', 'verb'])
REQUEST_COUNT = pc.Counter('http_request_count', 'Number of HTTP requests', ['endpoint', 'verb', 'status'])


def monitor_endpoint(match_info_labels: List[str] = None):
    def wrap(handler):
        @wraps(handler)
        async def wrapped(request, *args, **kwargs):
            # Use the path template given to @route.<METHOD>, not the fully resolved one
            endpoint = request.match_info.route.resource.canonical
            match_labels = {label: request.match_info[label] for label in match_info_labels}
            verb = request.method
            latency_metric = REQUEST_TIME.labels(endpoint=endpoint, verb=verb, **match_labels)
            response = await prom_async_time(latency_metric, handler(request, *args, **kwargs))
            REQUEST_COUNT.labels(endpoint=endpoint, verb=verb, status=response.status).inc()
            return response
        return wrapped
    return wrap
