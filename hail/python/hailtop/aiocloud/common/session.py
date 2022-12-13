from types import TracebackType
from typing import Optional, Type, TypeVar, Mapping
import abc
from hailtop import httpx
from hailtop.utils import RateLimit, RateLimiter
from .credentials import CloudCredentials

SessionType = TypeVar('SessionType', bound='BaseSession')


class BaseSession(abc.ABC):
    @abc.abstractmethod
    def request(self, method: str, url: str, **kwargs) -> httpx.ClientResponse:
        pass

    def get(self, url: str, **kwargs) -> httpx.ClientResponse:
        return self.request('GET', url, **kwargs)

    def post(self, url: str, **kwargs) -> httpx.ClientResponse:
        return self.request('POST', url, **kwargs)

    def put(self, url: str, **kwargs) -> httpx.ClientResponse:
        return self.request('PUT', url, **kwargs)

    def delete(self, url: str, **kwargs) -> httpx.ClientResponse:
        return self.request('DELETE', url, **kwargs)

    def head(self, url: str, **kwargs) -> httpx.ClientResponse:
        return self.request('HEAD', url, **kwargs)

    def close(self) -> None:
        pass

    def __enter__(self: SessionType) -> SessionType:
        return self

    def __exit__(self,
                 exc_type: Optional[Type[BaseException]],
                 exc_val: Optional[BaseException],
                 exc_tb: Optional[TracebackType]) -> None:
        self.close()


class RateLimitedSession(BaseSession):
    _session: BaseSession

    def __init__(self, *, session: BaseSession, rate_limit: RateLimit):
        self._session = session
        self._rate_limiter = RateLimiter(rate_limit)

    async def request(self, method: str, url: str, **kwargs):
        async with self._rate_limiter:
            return await self._session.request(method, url, **kwargs)

    async def close(self) -> None:
        if hasattr(self._session, '_session'):
            await self._session.close()
            del self._session


class Session(BaseSession):
    _http_session: httpx.ClientSession
    _credentials: CloudCredentials

    def __init__(self,
                 *,
                 credentials: CloudCredentials,
                 params: Optional[Mapping[str, str]] = None,
                 http_session: Optional[httpx.ClientSession] = None,
                 **kwargs):
        if 'raise_for_status' not in kwargs:
            kwargs['raise_for_status'] = True
        self._params = params
        if http_session is not None:
            assert len(kwargs) == 0
            self._http_session = http_session
        else:
            self._http_session = httpx.ClientSession(**kwargs)
        self._credentials = credentials

    def request(self, method: str, url: str, **kwargs) -> httpx.ClientResponse:
        auth_headers = self._credentials.auth_headers()
        if auth_headers:
            if 'headers' in kwargs:
                kwargs['headers'].update(auth_headers)
            else:
                kwargs['headers'] = auth_headers

        if self._params:
            if 'params' in kwargs:
                request_params = kwargs['params']
            else:
                request_params = {}
                kwargs['params'] = request_params
            for k, v in self._params.items():
                if k not in request_params:
                    request_params[k] = v

        return self._http_session.request(method, url, **kwargs)._resp

    async def close(self) -> None:
        if hasattr(self, '_http_session'):
            await self._http_session.close()
            del self._http_session

        if hasattr(self, '_credentials'):
            await self._credentials.close()
            del self._credentials
