from typing import List, Optional
from typing_extensions import TypedDict

from .base_client import GoogleBaseClient


class GenerateAccessTokenResponse(TypedDict):
    accessToken: str
    expireTime: str


class GoogleIAmClient(GoogleBaseClient):
    def __init__(self, project, **kwargs):
        super().__init__(f'https://iam.googleapis.com/v1/projects/{project}', **kwargs)

    # https://cloud.google.com/iam/docs/reference/rest


class GoogleIamCredentialsClient(GoogleBaseClient):
    def __init__(self, **kwargs):
        super().__init__('https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts', **kwargs)

    async def generate_access_token(self, service_account_email: str, *, lifetime_seconds: Optional[int] = None, scopes: Optional[List[str]] = None) -> GenerateAccessTokenResponse:
        body = {
            'lifetime': f'{lifetime_seconds or 3600}s',
            'scope': scopes or ["https://www.googleapis.com/auth/cloud-platform"],
        }
        return await self.post(f'/{service_account_email}:generateAccessToken', json=body)
