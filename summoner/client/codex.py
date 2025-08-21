import asyncio
import json
from typing import Any, Dict, List, Optional, Union, Literal
from dataclasses import dataclass
from datetime import datetime
import aiohttp
from urllib.parse import urljoin
from collections import deque


@dataclass
class BlockResponse:
    """Represents a block in a chain."""
    shard_id: int
    tenant: str
    name: str
    block_idx: int
    values: List[Any]
    created_at: datetime


@dataclass
class ChainMetadata:
    """Represents chain metadata."""
    shard_id: int
    tenant: str
    name: str
    current_head: Optional[int]
    current_tail: Optional[int]
    block_count: int
    created_at: datetime
    updated_at: datetime
    is_deleting: bool


@dataclass
class ChainValidation:
    """Represents chain validation results."""
    is_valid: bool
    error_message: Optional[str]
    expected_head: Optional[int]
    actual_head: Optional[int]
    expected_tail: Optional[int]
    actual_tail: Optional[int]
    expected_count: int
    actual_count: int
    has_gaps: bool


@dataclass
class ChainStats:
    """Represents chain statistics."""
    shard_id: int
    tenant: str
    name: str
    block_count: int
    head_idx: Optional[int]
    tail_idx: Optional[int]
    chain_span: int
    created_at: datetime
    updated_at: datetime
    last_activity_age: int


class ChainsClientError(Exception):
    """Base exception for chains client errors."""
    pass


class AuthenticationError(ChainsClientError):
    """Raised when authentication fails."""
    pass


class ConflictError(ChainsClientError):
    """Raised when there's a conflict (409 status)."""
    pass


class NotFoundError(ChainsClientError):
    """Raised when a resource is not found (404 status)."""
    pass


class ValidationError(ChainsClientError):
    """Raised when request validation fails (400 status)."""
    pass


class AsyncChainsClient:
    """
    Async client for interacting with the Chains API.
    
    This client handles authentication and provides methods for all chain operations
    including appending/prepending blocks, retrieving blocks and metadata, and
    managing chain lifecycle.
    """
    
    def __init__(self, host_url: str, username: str, password: str):
        """
        Initialize the chains client.
        
        Args:
            host_url: Base URL of the API server (e.g., "https://api.example.com")
            username: Username for authentication
            password: Password for authentication
        """
        self.host_url = host_url.rstrip('/')
        self.username = username
        self.password = password
        self._jwt_token: Optional[str] = None
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def __aenter__(self):
        """Async context manager entry."""
        await self._ensure_session()
        await self._ensure_authenticated()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
    
    async def _ensure_session(self):
        """Ensure aiohttp session is created."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
    
    async def close(self):
        """Close the aiohttp session."""
        if self._session:
            await self._session.close()
            self._session = None
    
    async def _ensure_authenticated(self):
        """Ensure we have a valid JWT token."""
        if self._jwt_token is None:
            await self._authenticate()
    
    async def _authenticate(self):
        """Authenticate with username/password and store JWT token."""
        await self._ensure_session()
        
        url = urljoin(self.host_url, '/auth/login')
        data = {
            'username': self.username,
            'password': self.password
        }
        
        async with self._session.post(url, json=data) as response:
            if response.status == 401:
                raise AuthenticationError("Invalid credentials")
            elif response.status != 200:
                error_data = await response.json()
                raise ChainsClientError(f"Authentication failed: {error_data.get('error', 'Unknown error')}")
            
            result = await response.json()
            self._jwt_token = result['jwt']
    
    def _get_headers(self) -> Dict[str, str]:
        """Get headers with JWT token for authenticated requests."""
        if not self._jwt_token:
            raise AuthenticationError("Not authenticated")
        
        return {
            'Authorization': f'Bearer {self._jwt_token}',
            'Content-Type': 'application/json'
        }
    
    async def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Make an authenticated request to the API."""
        await self._ensure_session()
        await self._ensure_authenticated()
        
        url = urljoin(self.host_url, endpoint)
        headers = self._get_headers()
        
        async with self._session.request(
            method,
            url,
            json=data,
            params=params,
            headers=headers
        ) as response:
            response_data = await response.json()
            
            if response.status == 401:
                # Token might be expired, try to re-authenticate once
                self._jwt_token = None
                await self._ensure_authenticated()
                headers = self._get_headers()
                
                async with self._session.request(
                    method,
                    url,
                    json=data,
                    params=params,
                    headers=headers
                ) as retry_response:
                    if retry_response.status == 401:
                        raise AuthenticationError("Authentication failed")
                    response = retry_response
                    response_data = await response.json()
            
            if response.status == 400:
                raise ValidationError(response_data.get('error', 'Validation error'))
            elif response.status == 404:
                raise NotFoundError(response_data.get('error', 'Not found'))
            elif response.status == 409:
                raise ConflictError(response_data.get('error', 'Conflict'))
            elif response.status >= 400:
                raise ChainsClientError(f"API error: {response_data.get('error', 'Unknown error')}")
            
            return response_data
    
    @staticmethod
    def _parse_datetime(dt_str: str) -> datetime:
        """Parse datetime string from API response."""
        # Handle various datetime formats that might come from the API
        try:
            return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
        except ValueError:
            # Fallback for different formats
            return datetime.fromisoformat(dt_str)
    
    def _parse_block_response(self, data: Dict) -> BlockResponse:
        """Parse block response data into BlockResponse object."""
        return BlockResponse(
            shard_id=data['shardId'],
            tenant=data['tenant'],
            name=data['name'],
            block_idx=int(data['blockIdx']),
            values=data['values'],
            created_at=self._parse_datetime(data['createdAt'])
        )
    
    def _parse_chain_metadata(self, data: Dict) -> ChainMetadata:
        """Parse chain metadata into ChainMetadata object."""
        return ChainMetadata(
            shard_id=data['shardId'],
            tenant=data['tenant'],
            name=data['name'],
            current_head=int(data['currentHead']) if data['currentHead'] is not None else None,
            current_tail=int(data['currentTail']) if data['currentTail'] is not None else None,
            block_count=int(data['blockCount']),
            created_at=self._parse_datetime(data['createdAt']),
            updated_at=self._parse_datetime(data['updatedAt']),
            is_deleting=data['isDeleting']
        )
    
    # Chain Operations
    
    async def append_block(self, chain_name: str, values: List[Any]) -> BlockResponse:
        """
        Append a block to the tail of a chain.
        
        Args:
            chain_name: Name of the chain
            values: List of values to store in the block
            
        Returns:
            BlockResponse object representing the created block
        """
        data = {
            'chainName': chain_name,
            'values': values
        }
        
        response = await self._make_request('POST', '/chains/append', data=data)
        return self._parse_block_response(response['block'])
    
    async def prepend_block(self, chain_name: str, values: List[Any]) -> BlockResponse:
        """
        Prepend a block to the head of a chain.
        
        Args:
            chain_name: Name of the chain
            values: List of values to store in the block
            
        Returns:
            BlockResponse object representing the created block
        """
        data = {
            'chainName': chain_name,
            'values': values
        }
        
        response = await self._make_request('POST', '/chains/prepend', data=data)
        return self._parse_block_response(response['block'])
    
    async def get_chain_metadata(self, chain_name: str) -> ChainMetadata:
        """
        Get metadata for a chain.
        
        Args:
            chain_name: Name of the chain
            
        Returns:
            ChainMetadata object
        """
        response = await self._make_request('GET', f'/chains/metadata/{chain_name}')
        return self._parse_chain_metadata(response['chain'])
    
    async def get_block(self, chain_name: str, block_idx: int) -> BlockResponse:
        """
        Get a specific block by index.
        
        Args:
            chain_name: Name of the chain
            block_idx: Index of the block
            
        Returns:
            BlockResponse object
        """
        response = await self._make_request('GET', f'/chains/block/{chain_name}/{block_idx}')
        return self._parse_block_response(response['block'])
    
    async def get_first_block(self, chain_name: str) -> BlockResponse:
        """
        Get the first block (head) of a chain.
        
        Args:
            chain_name: Name of the chain
            
        Returns:
            BlockResponse object
        """
        response = await self._make_request('GET', f'/chains/first/{chain_name}')
        return self._parse_block_response(response['block'])
    
    async def get_last_block(self, chain_name: str) -> BlockResponse:
        """
        Get the last block (tail) of a chain.
        
        Args:
            chain_name: Name of the chain
            
        Returns:
            BlockResponse object
        """
        response = await self._make_request('GET', f'/chains/last/{chain_name}')
        return self._parse_block_response(response['block'])
    
    async def get_blocks_range(
        self,
        chain_name: str,
        start_idx: int,
        end_idx: int,
        order: Literal["ASC", "DESC"] = "ASC",
        limit: Optional[int] = None
    ) -> List[BlockResponse]:
        """
        Get a range of blocks from a chain.
        
        Args:
            chain_name: Name of the chain
            start_idx: Starting block index
            end_idx: Ending block index
            order: Sort order ("ASC" or "DESC")
            limit: Maximum number of blocks to return (optional)
            
        Returns:
            List of BlockResponse objects
        """
        params = {
            'startIdx': start_idx,
            'endIdx': end_idx,
            'order': order
        }
        
        if limit is not None:
            params['limit'] = limit
        
        response = await self._make_request('GET', f'/chains/range/{chain_name}', params=params)
        return [self._parse_block_response(block) for block in response['blocks']]
    
    async def get_recent_blocks(self, chain_name: str, count: int = 10) -> List[BlockResponse]:
        """
        Get recent blocks from a chain.
        
        Args:
            chain_name: Name of the chain
            count: Number of recent blocks to retrieve (max 100)
            
        Returns:
            List of BlockResponse objects
        """
        params = {'count': count}
        response = await self._make_request('GET', f'/chains/recent/{chain_name}', params=params)
        return [self._parse_block_response(block) for block in response['blocks']]
    
    async def delete_chain(self, chain_name: str) -> ChainMetadata:
        """
        Delete an entire chain and all its blocks.
        
        Args:
            chain_name: Name of the chain to delete
            
        Returns:
            ChainMetadata object of the deleted chain
        """
        response = await self._make_request('DELETE', f'/chains/{chain_name}')
        return self._parse_chain_metadata(response['deletedChain'])
    
    async def validate_chain(self, chain_name: str) -> ChainValidation:
        """
        Validate chain integrity.
        
        Args:
            chain_name: Name of the chain to validate
            
        Returns:
            ChainValidation object with validation results
        """
        response = await self._make_request('GET', f'/chains/validate/{chain_name}')
        validation_data = response['validation']
        
        return ChainValidation(
            is_valid=validation_data['isValid'],
            error_message=validation_data.get('errorMessage'),
            expected_head=validation_data.get('expectedHead'),
            actual_head=validation_data.get('actualHead'),
            expected_tail=validation_data.get('expectedTail'),
            actual_tail=validation_data.get('actualTail'),
            expected_count=validation_data['expectedCount'],
            actual_count=validation_data['actualCount'],
            has_gaps=validation_data['hasGaps']
        )
    
    async def get_chain_stats(self, tenant: str) -> List[ChainStats]:
        """
        Get statistics for all chains in a tenant.
        
        Args:
            tenant: Tenant identifier
            
        Returns:
            List of ChainStats objects
        """
        response = await self._make_request('GET', f'/chains/stats/{tenant}')
        return [
            ChainStats(
                shard_id=stat['shardId'],
                tenant=stat['tenant'],
                name=stat['name'],
                block_count=stat['blockCount'],
                head_idx=stat['headIdx'],
                tail_idx=stat['tailIdx'],
                chain_span=stat['chainSpan'],
                created_at=self._parse_datetime(stat['createdAt']),
                updated_at=self._parse_datetime(stat['updatedAt']),
                last_activity_age=stat['lastActivityAge']
            )
            for stat in response['stats']
        ]
    
    async def iterate_blocks(
        self, 
        chain_name: str, 
        stop_on_missing: bool = True,
        poll_interval: float = 1.0,
        adaptive_batching: bool = True,
        initial_batch_size: int = 10,
        max_batch_size: int = 1000,
        max_queue_depth: int = 100
    ):
        """
        Async generator that yields blocks from head to tail with adaptive batching.
        
        This generator uses an internal queue and adaptive batch sizing to optimize
        performance. It automatically adjusts batch sizes based on queue depth:
        - Doubles batch size when queue is low (more aggressive prefetching)
        - Halves batch size when queue is high (reduce memory pressure)
        
        Args:
            chain_name: Name of the chain to iterate
            stop_on_missing: If True, stop when a block is not found.
                           If False, poll indefinitely for new blocks.
            poll_interval: Seconds to wait between polls when stop_on_missing=False
                          and we've reached the current tail
            adaptive_batching: If True, use adaptive batch sizing. If False, use 
                             initial_batch_size for all requests.
            initial_batch_size: Starting batch size for range queries
            max_batch_size: Maximum allowed batch size
            max_queue_depth: Target maximum queue depth before reducing batch size
            
        Yields:
            BlockResponse objects from head to tail
            
        Example:
            # Basic usage with adaptive batching
            async for block in client.iterate_blocks("my-chain"):
                print(f"Block {block.block_idx}: {block.values}")
            
            # Custom batching parameters
            async for block in client.iterate_blocks(
                "my-chain", 
                adaptive_batching=True,
                initial_batch_size=5,
                max_batch_size=500,
                max_queue_depth=50
            ):
                print(f"Block {block.block_idx}: {block.values}")
        """
        # Internal state for adaptive batching
        block_queue = deque()
        current_batch_size = initial_batch_size if adaptive_batching else initial_batch_size
        current_idx = None
        queue_hits = 0  # Track consecutive queue hits for batch size adjustment
        queue_misses = 0  # Track consecutive queue misses for batch size adjustment
        
        def adjust_batch_size():
            """Adjust batch size based on queue performance."""
            nonlocal current_batch_size, queue_hits, queue_misses
            
            if not adaptive_batching:
                return
                
            queue_depth = len(block_queue)
            
            # If queue is getting full, reduce batch size
            if queue_depth >= max_queue_depth:
                queue_hits += 1
                queue_misses = 0
                if queue_hits >= 2 and current_batch_size > 1:
                    current_batch_size = max(1, current_batch_size // 2)
                    queue_hits = 0
            
            # If queue is getting empty, increase batch size
            elif queue_depth <= max_queue_depth // 4:
                queue_misses += 1
                queue_hits = 0
                if queue_misses >= 2 and current_batch_size < max_batch_size:
                    current_batch_size = min(max_batch_size, current_batch_size * 2)
                    queue_misses = 0
            
            # Reset counters if we're in the middle range
            else:
                queue_hits = 0
                queue_misses = 0
        
        async def fill_queue_from_range(start_idx: int, end_idx: int):
            """Fill the queue with blocks from a range."""
            try:
                blocks = await self.get_blocks_range(
                    chain_name, start_idx, end_idx, order="ASC"
                )
                for block in blocks:
                    block_queue.append(block)
                return len(blocks)
            except (NotFoundError, ChainsClientError):
                return 0
        
        async def fill_queue_batch():
            """Fill the queue with the next batch of blocks."""
            nonlocal current_idx
            
            if current_idx is None:
                # Initialize - get chain metadata to find head
                try:
                    metadata = await self.get_chain_metadata(chain_name)
                    if metadata.current_head is None:
                        return 0  # Empty chain
                    current_idx = metadata.current_head
                except NotFoundError:
                    return 0  # Chain doesn't exist
            
            # Calculate the range for this batch
            start_idx = current_idx
            end_idx = current_idx + current_batch_size - 1
            
            # Try to fill the queue with this batch
            blocks_added = await fill_queue_from_range(start_idx, end_idx)
            
            if blocks_added > 0:
                # Update current_idx to the next block after what we loaded
                # Find the highest block_idx we actually loaded
                max_loaded_idx = max(block.block_idx for block in list(block_queue)[-blocks_added:])
                current_idx = max_loaded_idx + 1
            
            return blocks_added
        
        try:
            # Main iteration loop
            while True:
                # If queue is getting low, try to fill it
                if len(block_queue) <= max_queue_depth // 4:
                    blocks_added = await fill_queue_batch()
                    
                    if blocks_added == 0:
                        # No more blocks available
                        if stop_on_missing:
                            break
                        else:
                            # In infinite mode, wait and try again
                            await asyncio.sleep(poll_interval)
                            continue
                
                # Adjust batch size based on queue performance
                adjust_batch_size()
                
                # Yield blocks from the queue
                if block_queue:
                    block = block_queue.popleft()
                    yield block
                else:
                    # Queue is empty
                    if stop_on_missing:
                        break
                    else:
                        # In infinite mode, wait and try to fill queue again
                        await asyncio.sleep(poll_interval)
                        continue
        
        except NotFoundError:
            # Chain doesn't exist
            if stop_on_missing:
                return
            
            # In infinite mode, poll for chain to be created
            while True:
                try:
                    await asyncio.sleep(poll_interval)
                    metadata = await self.get_chain_metadata(chain_name)
                    if metadata.current_head is not None:
                        # Chain now exists, restart iteration
                        current_idx = None  # Reset to trigger re-initialization
                        async for block in self.iterate_blocks(
                            chain_name, stop_on_missing, poll_interval,
                            adaptive_batching, current_batch_size, max_batch_size, max_queue_depth
                        ):
                            yield block
                        return
                except NotFoundError:
                    continue
        
        except ChainsClientError:
            # For other errors, stop iteration
            return
    
    def chain(self, chain_name: str) -> 'ChainClient':
        """
        Create a focused client for a specific chain.
        
        Args:
            chain_name: Name of the chain to focus on
            
        Returns:
            ChainClient instance focused on the specified chain
            
        Example:
            async with AsyncChainsClient("https://api.example.com", "user", "pass") as client:
                my_chain = client.chain("my-chain")
                await my_chain.append(["data", 123])
                metadata = await my_chain.get_metadata()
        """
        return ChainClient(self, chain_name)


class ChainClient:
    """
    A focused client for working with a specific chain.
    
    This class provides a simplified interface by wrapping an AsyncChainsClient
    and automatically passing the chain name to all operations.
    """
    
    def __init__(self, client: AsyncChainsClient, chain_name: str):
        """
        Initialize a focused chain client.
        
        Args:
            client: The parent AsyncChainsClient instance
            chain_name: Name of the chain this client focuses on
        """
        self._client = client
        self._chain_name = chain_name
    
    @property
    def chain_name(self) -> str:
        """Get the chain name this client is focused on."""
        return self._chain_name
    
    @property
    def client(self) -> AsyncChainsClient:
        """Get the underlying AsyncChainsClient instance."""
        return self._client
    
    # Block Operations
    
    async def append(self, values: List[Any]) -> BlockResponse:
        """
        Append a block to the tail of this chain.
        
        Args:
            values: List of values to store in the block
            
        Returns:
            BlockResponse object representing the created block
        """
        return await self._client.append_block(self._chain_name, values)
    
    async def prepend(self, values: List[Any]) -> BlockResponse:
        """
        Prepend a block to the head of this chain.
        
        Args:
            values: List of values to store in the block
            
        Returns:
            BlockResponse object representing the created block
        """
        return await self._client.prepend_block(self._chain_name, values)
    
    # Metadata and Chain Info
    
    async def get_metadata(self) -> ChainMetadata:
        """
        Get metadata for this chain.
        
        Returns:
            ChainMetadata object
        """
        return await self._client.get_chain_metadata(self._chain_name)
    
    async def exists(self) -> bool:
        """
        Check if this chain exists.
        
        Returns:
            True if the chain exists, False otherwise
        """
        try:
            await self.get_metadata()
            return True
        except NotFoundError:
            return False
    
    async def get_block_count(self) -> int:
        """
        Get the number of blocks in this chain.
        
        Returns:
            Number of blocks in the chain
        """
        metadata = await self.get_metadata()
        return metadata.block_count
    
    async def is_empty(self) -> bool:
        """
        Check if this chain is empty (has no blocks).
        
        Returns:
            True if the chain is empty, False otherwise
        """
        try:
            metadata = await self.get_metadata()
            return metadata.block_count == 0
        except NotFoundError:
            return True
    
    # Block Retrieval
    
    async def get_block(self, block_idx: int) -> BlockResponse:
        """
        Get a specific block by index.
        
        Args:
            block_idx: Index of the block
            
        Returns:
            BlockResponse object
        """
        return await self._client.get_block(self._chain_name, block_idx)
    
    async def get_first_block(self) -> BlockResponse:
        """
        Get the first block (head) of this chain.
        
        Returns:
            BlockResponse object
        """
        return await self._client.get_first_block(self._chain_name)
    
    async def get_last_block(self) -> BlockResponse:
        """
        Get the last block (tail) of this chain.
        
        Returns:
            BlockResponse object
        """
        return await self._client.get_last_block(self._chain_name)
    
    async def get_blocks_range(
        self,
        start_idx: int,
        end_idx: int,
        order: Literal["ASC", "DESC"] = "ASC",
        limit: Optional[int] = None
    ) -> List[BlockResponse]:
        """
        Get a range of blocks from this chain.
        
        Args:
            start_idx: Starting block index
            end_idx: Ending block index
            order: Sort order ("ASC" or "DESC")
            limit: Maximum number of blocks to return (optional)
            
        Returns:
            List of BlockResponse objects
        """
        return await self._client.get_blocks_range(
            self._chain_name, start_idx, end_idx, order, limit
        )
    
    async def get_recent_blocks(self, count: int = 10) -> List[BlockResponse]:
        """
        Get recent blocks from this chain.
        
        Args:
            count: Number of recent blocks to retrieve (max 100)
            
        Returns:
            List of BlockResponse objects
        """
        return await self._client.get_recent_blocks(self._chain_name, count)
    
    async def get_all_blocks(self, order: Literal["ASC", "DESC"] = "ASC") -> List[BlockResponse]:
        """
        Get all blocks in this chain.
        
        Args:
            order: Sort order ("ASC" or "DESC")
            
        Returns:
            List of all BlockResponse objects in the chain
        """
        try:
            metadata = await self.get_metadata()
            if metadata.block_count == 0:
                return []
            
            # Use a very wide range to get all blocks
            return await self.get_blocks_range(
                metadata.current_head, metadata.current_tail, order
            )
        except NotFoundError:
            return []
    
    # Chain Management
    
    async def delete(self) -> ChainMetadata:
        """
        Delete this entire chain and all its blocks.
        
        Returns:
            ChainMetadata object of the deleted chain
        """
        return await self._client.delete_chain(self._chain_name)
    
    async def validate(self) -> ChainValidation:
        """
        Validate this chain's integrity.
        
        Returns:
            ChainValidation object with validation results
        """
        return await self._client.validate_chain(self._chain_name)
    
    # Iteration
    
    async def iterate_blocks(
        self, 
        stop_on_missing: bool = True,
        poll_interval: float = 1.0,
        adaptive_batching: bool = True,
        initial_batch_size: int = 10,
        max_batch_size: int = 1000,
        max_queue_depth: int = 100
    ):
        """
        Async generator that yields blocks from head to tail with adaptive batching.
        
        Args:
            stop_on_missing: If True, stop when a block is not found.
                           If False, poll indefinitely for new blocks.
            poll_interval: Seconds to wait between polls when stop_on_missing=False
                          and we've reached the current tail
            adaptive_batching: If True, use adaptive batch sizing. If False, use 
                             initial_batch_size for all requests.
            initial_batch_size: Starting batch size for range queries
            max_batch_size: Maximum allowed batch size
            max_queue_depth: Target maximum queue depth before reducing batch size
            
        Yields:
            BlockResponse objects from head to tail
        """
        async for block in self._client.iterate_blocks(
            self._chain_name, stop_on_missing, poll_interval,
            adaptive_batching, initial_batch_size, max_batch_size, max_queue_depth
        ):
            yield block
    
    # Convenience Methods
    
    async def append_multiple(self, values_list: List[List[Any]]) -> List[BlockResponse]:
        """
        Append multiple blocks in sequence.
        
        Args:
            values_list: List of value lists, each representing a block
            
        Returns:
            List of BlockResponse objects for the created blocks
        """
        blocks = []
        for values in values_list:
            block = await self.append(values)
            blocks.append(block)
        return blocks
    
    async def prepend_multiple(self, values_list: List[List[Any]]) -> List[BlockResponse]:
        """
        Prepend multiple blocks in sequence.
        
        Args:
            values_list: List of value lists, each representing a block
            
        Returns:
            List of BlockResponse objects for the created blocks
        """
        blocks = []
        for values in values_list:
            block = await self.prepend(values)
            blocks.append(block)
        return blocks
    
    async def find_blocks_with_value(self, target_value: Any) -> List[BlockResponse]:
        """
        Find all blocks that contain a specific value.
        
        Args:
            target_value: The value to search for in block values
            
        Returns:
            List of BlockResponse objects containing the target value
        """
        matching_blocks = []
        all_blocks = await self.get_all_blocks()
        
        for block in all_blocks:
            if target_value in block.values:
                matching_blocks.append(block)
        
        return matching_blocks
    
    async def get_block_by_creation_time(self, target_time: datetime) -> Optional[BlockResponse]:
        """
        Find the block closest to a specific creation time.
        
        Args:
            target_time: The target datetime to search for
            
        Returns:
            BlockResponse object closest to the target time, or None if chain is empty
        """
        all_blocks = await self.get_all_blocks()
        if not all_blocks:
            return None
        
        # Find block with minimum time difference
        closest_block = min(
            all_blocks,
            key=lambda block: abs((block.created_at - target_time).total_seconds())
        )
        
        return closest_block
    
    def __repr__(self) -> str:
        """String representation of the ChainClient."""
        return f"ChainClient(chain_name='{self._chain_name}')"


# Example usage
async def example_usage():
    """Example demonstrating how to use the AsyncChainsClient."""
    
    # Using async context manager (recommended)
    async with AsyncChainsClient("https://api.example.com", "username", "password") as client:
        
        # Append a block to a chain
        block = await client.append_block("my-chain", ["hello", "world", 42])
        print(f"Created block {block.block_idx} with values: {block.values}")
        
        # Get chain metadata
        metadata = await client.get_chain_metadata("my-chain")
        print(f"Chain has {metadata.block_count} blocks")
        
        # Get recent blocks
        recent_blocks = await client.get_recent_blocks("my-chain", count=5)
        print(f"Retrieved {len(recent_blocks)} recent blocks")
        
        # Get a range of blocks
        blocks = await client.get_blocks_range("my-chain", 0, 10, order="DESC")
        print(f"Retrieved {len(blocks)} blocks in range")
        
        # Validate chain integrity
        validation = await client.validate_chain("my-chain")
        print(f"Chain is valid: {validation.is_valid}")
        
        # Iterate through all existing blocks (stops when no more blocks)
        print("\nIterating existing blocks:")
        async for block in client.iterate_blocks("my-chain", stop_on_missing=True):
            print(f"Block {block.block_idx}: {block.values}")
        
        # Example of infinite iteration (commented out to avoid infinite loop in example)
        # print("\nWaiting for new blocks (infinite mode):")
        # async for block in client.iterate_blocks("my-chain", stop_on_missing=False, poll_interval=2.0):
        #     print(f"New block {block.block_idx}: {block.values}")


async def example_focused_chain_usage():
    """Example demonstrating the focused ChainClient."""
    
    async with AsyncChainsClient("https://api.example.com", "username", "password") as client:
        
        # Create a focused client for a specific chain
        my_chain = client.chain("my-important-chain")
        
        # Much cleaner API - no need to repeat chain name
        await my_chain.append(["first", "block"])
        await my_chain.append(["second", "block"])
        await my_chain.prepend(["zeroth", "block"])
        
        # Get chain info
        metadata = await my_chain.get_metadata()
        print(f"Chain '{my_chain.chain_name}' has {metadata.block_count} blocks")
        
        # Check if chain exists and is empty
        exists = await my_chain.exists()
        empty = await my_chain.is_empty()
        print(f"Exists: {exists}, Empty: {empty}")
        
        # Get blocks
        first_block = await my_chain.get_first_block()
        last_block = await my_chain.get_last_block()
        all_blocks = await my_chain.get_all_blocks()
        
        print(f"First block: {first_block.values}")
        print(f"Last block: {last_block.values}")
        print(f"All blocks: {len(all_blocks)} total")
        
        # Convenience methods
        await my_chain.append_multiple([
            ["batch", "block", 1],
            ["batch", "block", 2],
            ["batch", "block", 3]
        ])
        
        # Find blocks containing specific values
        blocks_with_batch = await my_chain.find_blocks_with_value("batch")
        print(f"Found {len(blocks_with_batch)} blocks containing 'batch'")
        
        # Iterate blocks
        print("\nIterating through chain blocks:")
        async for block in my_chain.iterate_blocks(stop_on_missing=True):
            print(f"  Block {block.block_idx}: {block.values}")
        
        # Validate and clean up
        validation = await my_chain.validate()
        print(f"Chain validation: {validation.is_valid}")
        
        # You can still access the underlying client if needed
        stats = await my_chain.client.get_chain_stats("some-tenant")
        print(f"Tenant has {len(stats)} chains")


async def example_multiple_focused_chains():
    """Example using multiple focused chain clients."""
    
    async with AsyncChainsClient("https://api.example.com", "username", "password") as client:
        
        # Create multiple focused clients
        user_events = client.chain("user-events")
        system_logs = client.chain("system-logs")
        transactions = client.chain("transactions")
        
        # Work with each chain independently
        await user_events.append(["user_login", "user123", "2025-01-01"])
        await system_logs.append(["info", "System started"])
        await transactions.append(["payment", 100.50, "USD"])
        
        # Check each chain
        chains = [user_events, system_logs, transactions]
        for chain in chains:
            count = await chain.get_block_count()
            print(f"Chain '{chain.chain_name}': {count} blocks")


async def example_adaptive_batching():
    """Example demonstrating adaptive batching features."""
    
    async with AsyncChainsClient("https://api.example.com", "username", "password") as client:
        
        my_chain = client.chain("large-chain")
        
        print("=== Adaptive Batching Examples ===\n")
        
        # Example 1: Default adaptive batching
        print("1. Default adaptive batching:")
        block_count = 0
        async for block in my_chain.iterate_blocks(
            stop_on_missing=True,
            adaptive_batching=True  # Default settings
        ):
            block_count += 1
            if block_count <= 5:  # Just show first few
                print(f"   Block {block.block_idx}: {block.values}")
            elif block_count == 6:
                print(f"   ... (showing first 5 of {block_count}+ blocks)")
        
        print(f"   Total blocks processed: {block_count}\n")
        
        # Example 2: Custom batching parameters
        print("2. Custom batching (small batches, low queue depth):")
        async for block in my_chain.iterate_blocks(
            stop_on_missing=True,
            adaptive_batching=True,
            initial_batch_size=5,     # Start small
            max_batch_size=50,        # Don't get too big
            max_queue_depth=20        # Keep queue small
        ):
            print(f"   Block {block.block_idx}: {len(block.values)} values")
            break  # Just show the first one
        
        print()
        
        # Example 3: Fixed batch size (no adaptation)
        print("3. Fixed batch size (no adaptation):")
        block_count = 0
        async for block in my_chain.iterate_blocks(
            stop_on_missing=True,
            adaptive_batching=False,  # Disable adaptation
            initial_batch_size=25     # Fixed size
        ):
            block_count += 1
            if block_count <= 3:
                print(f"   Block {block.block_idx}: {block.values}")
            if block_count >= 3:
                break
        
        print()
        
        # Example 4: Live streaming with adaptive batching
        print("4. Live streaming mode (commented out to avoid infinite loop):")
        print("   # This would adapt batch sizes in real-time as new blocks arrive")
        # async for block in my_chain.iterate_blocks(
        #     stop_on_missing=False,    # Infinite mode
        #     adaptive_batching=True,
        #     poll_interval=0.5         # Fast polling
        # ):
        #     print(f"   Live block {block.block_idx}: {block.values}")


async def example_performance_tuning():
    """Example showing how to tune adaptive batching for different scenarios."""
    
    async with AsyncChainsClient("https://api.example.com", "username", "password") as client:
        
        print("=== Performance Tuning Examples ===\n")
        
        # Scenario 1: High-throughput processing (large batches, big queue)
        high_throughput_chain = client.chain("high-volume-data")
        print("1. High-throughput configuration:")
        print("   - Large initial batch size (100)")
        print("   - High max batch size (1000)")
        print("   - Deep queue (200)")
        
        # async for block in high_throughput_chain.iterate_blocks(
        #     adaptive_batching=True,
        #     initial_batch_size=100,
        #     max_batch_size=1000,
        #     max_queue_depth=200
        # ):
        #     # Process blocks rapidly
        #     pass
        
        # Scenario 2: Memory-constrained processing (small batches, shallow queue)
        memory_constrained_chain = client.chain("memory-sensitive")
        print("\n2. Memory-constrained configuration:")
        print("   - Small initial batch size (5)")
        print("   - Low max batch size (25)")
        print("   - Shallow queue (10)")
        
        # async for block in memory_constrained_chain.iterate_blocks(
        #     adaptive_batching=True,
        #     initial_batch_size=5,
        #     max_batch_size=25,
        #     max_queue_depth=10
        # ):
        #     # Process with minimal memory usage
        #     pass
        
        # Scenario 3: Real-time processing (balanced settings)
        realtime_chain = client.chain("realtime-events")
        print("\n3. Real-time processing configuration:")
        print("   - Medium initial batch size (20)")
        print("   - Reasonable max batch size (200)")
        print("   - Balanced queue (50)")
        print("   - Fast polling (0.1s)")
        
        # async for block in realtime_chain.iterate_blocks(
        #     stop_on_missing=False,  # Live mode
        #     adaptive_batching=True,
        #     initial_batch_size=20,
        #     max_batch_size=200,
        #     max_queue_depth=50,
        #     poll_interval=0.1
        # ):
        #     # Process events in near real-time
        #     pass


if __name__ == "__main__":
    asyncio.run(example_usage())