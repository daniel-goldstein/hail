from types import TracebackType
from typing import Any, Optional, Type, TypeVar
from hailtop.utils import RateLimit

from .session import BaseSession, RateLimitedSession

ClientType = TypeVar('ClientType', bound='CloudBaseClient')


class CloudBaseClient:
    _session: BaseSession

    def __init__(self, base_url: str, session: BaseSession, *, rate_limit: Optional[RateLimit] = None):
        self._base_url = base_url
        if rate_limit is not None:
            session = RateLimitedSession(session=session, rate_limit=rate_limit)
        self._session = session

    def get(self, path: Optional[str] = None, *, url: Optional[str] = None, **kwargs) -> Any:
        if url is None:
            assert path
            url = f'{self._base_url}{path}'
        with self._session.get(url, **kwargs) as resp:
            return resp.json()

    def post(self, path: Optional[str] = None, *, url: Optional[str] = None, **kwargs) -> Any:
        if url is None:
            assert path
            url = f'{self._base_url}{path}'
        with self._session.post(url, **kwargs) as resp:
            return resp.json()

    def delete(self, path: Optional[str] = None, *, url: Optional[str] = None, **kwargs) -> Any:
        if url is None:
            assert path
            url = f'{self._base_url}{path}'
        with self._session.delete(url, **kwargs) as resp:
            return resp.json()

    def put(self, path: Optional[str] = None, *, url: Optional[str] = None, **kwargs) -> Any:
        if url is None:
            assert path
            url = f'{self._base_url}{path}'
        with self._session.put(url, **kwargs) as resp:
            return resp.json()

    def close(self) -> None:
        if hasattr(self, '_session'):
            self._session.close()
            del self._session

    def __enter__(self: ClientType) -> ClientType:
        return self

    def __exit__(self,
                        exc_type: Optional[Type[BaseException]],
                        exc_val: Optional[BaseException],
                        exc_tb: Optional[TracebackType]) -> None:
        self.close()
