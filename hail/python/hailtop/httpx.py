from typing import Any, Tuple, Optional, Type, TypeVar, Generic, Callable, Union
from types import TracebackType
from urllib.parse import urlencode
import json

from .utils import async_to_blocking

from js import XMLHttpRequest, Blob


class ClientTimeout:
    def __init__(self, total: int):
        self.total = total


class ClientResponseError(BaseException):
    def __init__(self, req: XMLHttpRequest):
        self.req = req
        self.status = req.status
        self.message = req.responseText
        self.url = req.responseURL
        self.body = ''

    def __str__(self) -> str:
        return (f"{self.status}, message={self.message!r}, "
                f"url={self.url!r} body={self.body!r}")

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.__str__()})"


class ClientResponse:
    def __init__(self, xml_http_req: XMLHttpRequest):
        self._xml_http_req = xml_http_req
        self._response = str(xml_http_req.response)
        self._status = xml_http_req.status

    def release(self) -> None:
        pass

    @property
    def closed(self) -> bool:
        return True

    def close(self) -> None:
        pass

    def read(self) -> bytes:
        return self._response.encode('utf-8')

    def text(self, encoding: Optional[str] = None, errors: str = 'strict'):
        return self._response

    def json(self):
        return json.loads(self._response)

    @property
    def headers(self):
        return {}

    def header(self, header_name):
        return self._xml_http_req.getResponseHeader(header_name)

    @property
    def status(self):
        return self._status

    def __enter__(self) -> "ClientResponse":
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        self.release()


class _RequestContextManager:
    def __init__(self, resp):
        self._resp = resp

    def __enter__(self):
        return self._resp

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        pass


class IterablePayload:
    def __init__(self, value, *args: Any, **kwargs: Any) -> None:
        if "content_type" not in kwargs:
            kwargs["content_type"] = "application/octet-stream"
        self._iter = iter(value)

    def write(self, buf) -> None:
        if self._iter:
            try:
                # iter is not None check prevents rare cases
                # when the case iterable is used twice
                while True:
                    chunk = next(self._iter)
                    buf.extend(chunk)
            except StopIteration:
                self._iter = None


class ClientSession:
    def __init__(self,
                 *args,
                 raise_for_status: bool = True,
                 timeout: Union[ClientTimeout, float, None] = None,
                 **kwargs):
        self.raise_for_status = raise_for_status

    def request(self, method: str, url, **kwargs: Any):
        def request_and_raise_for_status():
            json_data = kwargs.pop('json', None)
            if json_data is not None:
                if kwargs.get('data') is not None:
                    raise ValueError(
                        'data and json parameters cannot be used at the same time')
                blob = Blob.new([json.dumps(json_data)], {type: 'application/json'})
            elif 'data' in kwargs:
                data = kwargs['data']
                if isinstance(data, IterablePayload):
                    buf = bytearray()
                    while data._iter is not None:
                        data.write(buf)
                    data = bytes(buf)
                blob = Blob.new([data])
            else:
                blob = Blob.new()

            if 'params' in kwargs and kwargs['params'] is not None:
                url_with_params = url + '?' + urlencode(kwargs['params'])
            else:
                url_with_params = url

            req = XMLHttpRequest.new()
            req.open(method, url_with_params, False)
            for header, val in kwargs.get('headers', {}).items():
                req.setRequestHeader(header, val)
            req.send(blob)
            if req.status >= 400:
                raise ClientResponseError(req)
            return ClientResponse(req)
        return _RequestContextManager(request_and_raise_for_status())

    def ws_connect(
        self, *args, **kwargs
    ):
        pass

    def get(
        self, url, *, allow_redirects: bool = True, **kwargs: Any
    ):
        return self.request('GET', url, allow_redirects=allow_redirects, **kwargs)

    def options(
        self, url, *, allow_redirects: bool = True, **kwargs: Any
    ):
        return self.request('OPTIONS', url, allow_redirects=allow_redirects, **kwargs)

    def head(
        self, url, *, allow_redirects: bool = False, **kwargs: Any
    ):
        return self.request('HEAD', url, allow_redirects=allow_redirects, **kwargs)

    def post(
        self, url, *, data: Any = None, **kwargs: Any
    ):
        return self.request('POST', url, data=data, **kwargs)

    def put(
        self, url, *, data: Any = None, **kwargs: Any
    ):
        return self.request('PUT', url, data=data, **kwargs)

    def patch(
        self, url, *, data: Any = None, **kwargs: Any
    ):
        return self.request('PATCH', url, data=data, **kwargs)

    def delete(
        self, url, **kwargs: Any
    ):
        return self.request('DELETE', url, **kwargs)

    async def close(self) -> None:
        pass

    @property
    def closed(self) -> bool:
        return False

    @property
    def cookie_jar(self):
        return None

    @property
    def version(self) -> Tuple[int, int]:
        return (0, 0)

    async def __aenter__(self) -> "ClientSession":
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        pass


def client_session(*args, **kwargs) -> ClientSession:
    return ClientSession(*args, **kwargs)


def blocking_client_session(*args, **kwargs) -> 'BlockingClientSession':
    return BlockingClientSession(client_session(*args, **kwargs))


class BlockingClientResponse:
    def __init__(self, client_response):
        self.client_response = client_response

    def read(self) -> bytes:
        return async_to_blocking(self.client_response.read())

    def text(self, encoding: Optional[str] = None, errors: str = 'strict') -> str:
        return async_to_blocking(self.client_response.text(
            encoding=encoding, errors=errors))

    def json(self, *,
             encoding: Optional[str] = None,
             loads,
             content_type: Optional[str] = 'application/json') -> Any:
        return async_to_blocking(self.client_response.json(
            encoding=encoding, loads=loads, content_type=content_type))

    def __del__(self):
        self.client_response.__del__()

    def history(self):
        return self.client_response.history

    def __repr__(self) -> str:
        return f'BlockingClientRepsonse({repr(self.client_response)})'

    @property
    def status(self) -> int:
        return self.client_response.status

    def raise_for_status(self) -> None:
        self.client_response.raise_for_status()


class BlockingClientWebSocketResponse:
    def __init__(self, ws):
        self.ws = ws

    @property
    def closed(self) -> bool:
        return self.ws.closed

    @property
    def close_code(self) -> Optional[int]:
        return self.ws.close_code

    @property
    def protocol(self) -> Optional[str]:
        return self.ws.protocol

    @property
    def compress(self) -> int:
        return self.ws.compress

    @property
    def client_notakeover(self) -> bool:
        return self.ws.client_notakeover

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        return self.ws.get_extra_info(name, default)

    def exception(self) -> Optional[BaseException]:
        return self.ws.exception()

    def ping(self, message: bytes = b'') -> None:
        async_to_blocking(self.ws.ping(message))

    def pong(self, message: bytes = b'') -> None:
        async_to_blocking(self.ws.pong(message))

    def send_str(self, data: str,
                 compress: Optional[int] = None) -> None:
        return async_to_blocking(self.ws.send_str(data, compress))

    def send_bytes(self, data: bytes,
                   compress: Optional[int] = None) -> None:
        return async_to_blocking(self.ws.send_bytes(data, compress))

    def send_json(self, data: Any,
                  compress: Optional[int] = None,
                  *, dumps) -> None:
        return async_to_blocking(self.ws.send_json(data, compress, dumps=dumps))

    def close(self, *, code: int = 1000, message: bytes = b'') -> bool:
        return async_to_blocking(self.ws.close(code=code, message=message))

    def receive(self, timeout: Optional[float] = None):
        return async_to_blocking(self.ws.receive(timeout))

    def receive_str(self, *, timeout: Optional[float] = None) -> str:
        return async_to_blocking(self.ws.receive_str(timeout=timeout))

    def receive_bytes(self, *, timeout: Optional[float] = None) -> bytes:
        return async_to_blocking(self.ws.receive_bytes(timeout=timeout))

    def receive_json(self,
                     *, loads,
                     timeout: Optional[float] = None) -> Any:
        return async_to_blocking(self.ws.receive_json(loads=loads, timeout=timeout))

    def __iter__(self) -> 'BlockingClientWebSocketResponse':
        return self

    def __next__(self):
        try:
            return async_to_blocking(self.ws.__anext__())
        except StopAsyncIteration as exc:
            raise StopIteration() from exc


T = TypeVar('T')  # pylint: disable=invalid-name
U = TypeVar('U')  # pylint: disable=invalid-name


class AsyncToBlockingContextManager(Generic[T, U]):
    def __init__(self, context_manager, wrap: Callable[[T], U]):
        self.context_manager = context_manager
        self.wrap = wrap

    def __enter__(self) -> U:
        return self.wrap(async_to_blocking(self.context_manager.__aenter__()))

    def __exit__(self,
                 exc_type: Optional[Type[BaseException]],
                 exc: Optional[BaseException],
                 tb: Optional[TracebackType]) -> None:
        async_to_blocking(self.context_manager.__aexit__(exc_type, exc, tb))


class BlockingClientResponseContextManager(AsyncToBlockingContextManager):
    def __init__(self, context_manager):
        super().__init__(context_manager, BlockingClientResponse)


class BlockingClientWebSocketResponseContextManager(AsyncToBlockingContextManager):
    def __init__(self, context_manager):
        super().__init__(context_manager, BlockingClientWebSocketResponse)


class BlockingClientSession:
    def __init__(self, session: ClientSession):
        self.session = session

    def request(self,
                method: str,
                url,
                **kwargs: Any) -> BlockingClientResponseContextManager:
        return BlockingClientResponseContextManager(
            self.session.request(method, url, **kwargs))

    def ws_connect(self,
                   url,
                   **kwargs: Any) -> BlockingClientWebSocketResponseContextManager:
        return BlockingClientWebSocketResponseContextManager(
            self.session.ws_connect(url, **kwargs))

    def get(self,
            url,
            *,
            allow_redirects: bool = True,
            **kwargs: Any) -> BlockingClientResponseContextManager:
        return BlockingClientResponseContextManager(
            self.session.get(url, allow_redirects=allow_redirects, **kwargs))

    def options(self,
                url,
                *,
                allow_redirects: bool = True,
                **kwargs: Any) -> BlockingClientResponseContextManager:
        return BlockingClientResponseContextManager(
            self.session.options(url, allow_redirects=allow_redirects, **kwargs))

    def head(self,
             url,
             *,
             allow_redirects: bool = False,
             **kwargs: Any) -> BlockingClientResponseContextManager:
        return BlockingClientResponseContextManager(self.session.head(
            url, allow_redirects=allow_redirects, **kwargs))

    def post(self,
             url,
             *,
             data: Any = None, **kwargs: Any) -> BlockingClientResponseContextManager:
        return BlockingClientResponseContextManager(self.session.post(
            url, data=data, **kwargs))

    def put(self,
            url,
            *,
            data: Any = None,
            **kwargs: Any) -> BlockingClientResponseContextManager:
        return BlockingClientResponseContextManager(self.session.put(
            url, data=data, **kwargs))

    def patch(self,
              url,
              *,
              data: Any = None,
              **kwargs: Any) -> BlockingClientResponseContextManager:
        return BlockingClientResponseContextManager(self.session.patch(
            url, data=data, **kwargs))

    def delete(self,
               url,
               **kwargs: Any) -> BlockingClientResponseContextManager:
        return BlockingClientResponseContextManager(self.session.delete(
            url, **kwargs))

    def close(self) -> None:
        async_to_blocking(self.session.close())

    @property
    def closed(self) -> bool:
        return self.session.closed

    @property
    def cookie_jar(self):
        return self.session.cookie_jar

    @property
    def version(self) -> Tuple[int, int]:
        return self.session.version

    def __enter__(self) -> 'BlockingClientSession':
        self.session = async_to_blocking(self.session.__aenter__())
        return self

    def __exit__(self,
                 exc_type: Optional[Type[BaseException]],
                 exc_val: Optional[BaseException],
                 exc_tb: Optional[TracebackType]) -> None:
        self.close()
