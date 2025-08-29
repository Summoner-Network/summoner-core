# Define a custom exception for clarity
import json
from typing import Any, Dict, Optional, Union

import httpx


class APIError(Exception):
    def __init__(self, message, status_code: int, response_text: str):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text

class _BaseClient:
    """
    An internal base class to handle shared state and the httpx request logic.
    Not intended for direct use.
    """
    def __init__(self, base_url: str):
        self._client = httpx.AsyncClient(base_url=base_url)
        self.token: Optional[str] = None
        self.user_id: Optional[str] = None
        self.username: Optional[str] = None

    async def _request(
        self,
        method: str,
        path: str,
        expected_status: int,
        json_body: Optional[Dict] = None,
        params: Optional[Dict] = None
    ) -> Any:
        """A private helper for making authenticated JSON requests using httpx."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        try:
            response = await self._client.request(
                method,
                path,
                headers=headers,
                json=json_body if json_body is not None else None,
                params=params if params is not None else None,
                timeout=10.0,
            )

            if response.status_code != expected_status:
                if path == "/api/auth/login" and response.status_code == 401:
                    raise APIError("Invalid credentials", 401, response.text)
                raise APIError(
                    f"API Error: Expected status {expected_status} but got {response.status_code}",
                    response.status_code,
                    response.text,
                )

            if response.status_code == 204: # No Content
                return None

            return response.json()

        except httpx.RequestError as e:
            raise APIError(f"HTTP request failed: {e}", 0, "") from e

    def _check_auth(self, method_name: str):
        if not self.token or not self.user_id or not self.username:
            raise RuntimeError(
                f"Must be logged in to call {method_name}. Please call login() first."
            )
            
    async def close(self):
        """Closes the underlying HTTP client session."""
        await self._client.aclose()

class SummonerSecurityAPIClient:
    """Handles authentication endpoints."""
    def __init__(self, parent_client: '_BaseClient'):
        self._client = parent_client

    async def login(self, creds: Dict[str, str]) -> None:
        username = creds.get("username")
        password = creds.get("password")
        if not username or not password:
            raise ValueError("Username and password are required")

        try:
            login_res = await self._client._request("POST", "/api/auth/login", 200, json_body=creds)
        except APIError as e:
            if e.status_code == 401:
                try:
                    await self._client._request("POST", "/api/auth/register", 201, json_body=creds)
                    login_res = await self._client._request("POST", "/api/auth/login", 200, json_body=creds)
                except APIError as reg_err:
                    raise APIError(
                        f"Login failed, and registration also failed with status {reg_err.status_code}",
                        reg_err.status_code,
                        reg_err.response_text
                    ) from reg_err
            else:
                raise e

        self._client.token = login_res.get("jwt")
        if not self._client.token:
            raise APIError("Login succeeded but did not return a JWT token", 200, json.dumps(login_res))

        me_res = await self._client._request("GET", "/api/account/me", 200)
        account_data = me_res.get("account", {})
        self._client.user_id = account_data.get("id")
        self._client.username = account_data.get("attrs", {}).get("username")

        if not self._client.user_id or not self._client.username:
            raise APIError("Successfully authenticated but failed to retrieve user details from /me", 200, json.dumps(me_res))

class SummonerObjectsAPIClient:
    """Handles BOSS (Objects & Associations) endpoints."""
    def __init__(self, parent_client: '_BaseClient'):
        self._client = parent_client

    async def get_object(self, otype: int, obj_id: Union[str, int]) -> Dict:
        self._client._check_auth("get_object")
        path = f"/api/objects/{self._client.user_id}/{otype}/{obj_id}"
        return await self._client._request("GET", path, 200)

    async def put_object(self, obj: Dict) -> Dict:
        self._client._check_auth("put_object")
        path = f"/api/objects/{self._client.user_id}"
        return await self._client._request("PUT", path, 201, json_body=obj)
        
    async def remove_object(self, otype: int, obj_id: Union[str, int]) -> Dict:
        self._client._check_auth("remove_object")
        path = f"/api/objects/{self._client.user_id}/{otype}/{obj_id}"
        return await self._client._request("DELETE", path, 200)

    async def get_associations(self, type: str, source_id: Union[str, int], params: Optional[Dict] = None) -> Dict:
        self._client._check_auth("get_associations")
        path = f"/api/objects/{self._client.user_id}/associations/{type}/{source_id}"
        return await self._client._request("GET", path, 200, params=params)

    async def put_association(self, association: Dict) -> Dict:
        self._client._check_auth("put_association")
        path = f"/api/objects/{self._client.user_id}/associations"
        return await self._client._request("PUT", path, 201, json_body=association)

    async def remove_association(self, type: str, source_id: Union[str, int], target_id: Union[str, int]) -> Dict:
        self._client._check_auth("remove_association")
        path = f"/api/objects/{self._client.user_id}/associations/{type}/{source_id}/{target_id}"
        return await self._client._request("DELETE", path, 200)

class SummonerChainsAPIClient:
    """Handles Fathom (Chains) endpoints."""
    def __init__(self, parent_client: '_BaseClient'):
        self._client = parent_client
        
    async def append(self, chain_key: Dict, data: Dict) -> Dict:
        self._client._check_auth("append")
        path = f"/api/chains/append/{self._client.username}/{chain_key['chainName']}/{chain_key['shardId']}"
        return await self._client._request("POST", path, 201, json_body=data)
        
    async def get_metadata(self, chain_key: Dict) -> Dict:
        self._client._check_auth("get_metadata")
        path = f"/api/chains/metadata/{self._client.username}/{chain_key['chainName']}/{chain_key['shardId']}"
        return await self._client._request("GET", path, 200)

    # ... Other chains methods like prepend, get_block, delete etc. would follow the same pattern ...


class SummonerAPIClient(_BaseClient):
    """
    A high-level async client for the Summoner REST API.
    
    This client composes specialized sub-clients for different API areas,
    accessible via `.auth`, `.boss`, and `.chains` attributes.
    """
    def __init__(self, base_url: str):
        if not base_url:
            raise ValueError("base_url is required")
        
        super().__init__(base_url)
        
        self.security = SummonerSecurityAPIClient(self)
        self.objects = SummonerObjectsAPIClient(self)
        self.chains = SummonerChainsAPIClient(self)

    async def login(self, creds: Dict[str, str]):
        """
        Authenticates the client via the auth sub-client.
        This populates the session state for all other sub-clients.
        """
        await self.security.login(creds)
    
    async def close(self):
        """Closes the underlying httpx client session."""
        await super().close()