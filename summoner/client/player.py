"""
Player module for spine-based agent integration with chains.

This module provides the Player class that acts as a bridge between agents
and their spine chains, enabling seamless integration with the blockchain
data structure for agent decision-making and state management.
"""

import asyncio
from dataclasses import dataclass
from typing import AsyncGenerator, Optional, Any, List
from datetime import datetime

from .codex import AsyncChainsClient, ChainClient, BlockResponse


@dataclass
class Login:
    """Authentication credentials for chain API access."""
    username: str
    password: str


class Player:
    """
    Player represents an agent with a dedicated spine chain for decision-making.
    
    The Player class provides a high-level interface for agents to interact with
    their spine chain - a dedicated blockchain that serves as the agent's "neural
    spine" for processing decisions, state changes, and coordination with other agents.
    
    Attributes:
        url: Base URL of the chains API server
        user: Login credentials for authentication
        chain: Name of the spine chain for this player/agent
    
    Example:
        async with Player("https://api.com", Login("agent", "pass"), "agent-spine") as player:
            async for message in player.integrate():
                await process_agent_decision(message)
    """
    url: str
    user: Login
    chain: str
    
    def __post_init__(self):
        """Initialize the chains client and focused chain client after dataclass creation."""
        self._chains_client: Optional[AsyncChainsClient] = None
        self._chain_client: Optional[ChainClient] = None
        self._session_active = False
        self._migrate_hooks = []  # List of (target_player, priority, hook_func)
    
    async def __aenter__(self):
        """Async context manager entry - initialize connections."""
        await self._ensure_connected()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - cleanup connections."""
        await self._cleanup()
    
    async def _ensure_connected(self):
        """Ensure we have active connections to the chains API."""
        if not self._session_active:
            self._chains_client = AsyncChainsClient(
                host_url=self.url,
                username=self.user.username,
                password=self.user.password
            )
            await self._chains_client.__aenter__()
            self._chain_client = self._chains_client.chain(self.chain)
            self._session_active = True
    
    async def _cleanup(self):
        """Clean up connections and resources."""
        if self._session_active and self._chains_client:
            await self._chains_client.__aexit__(None, None, None)
            self._chains_client = None
            self._chain_client = None
            self._session_active = False
    
    @property
    def spine(self) -> ChainClient:
        """
        Get the spine chain client for this player.
        
        The spine chain serves as the agent's primary data stream for decision-making,
        state management, and coordination with other agents or systems.
        
        Returns:
            ChainClient focused on this player's spine chain
            
        Raises:
            RuntimeError: If called before connecting (use async context manager)
        """
        if not self._session_active or not self._chain_client:
            raise RuntimeError(
                "Player not connected. Use 'async with player:' or call 'await player._ensure_connected()'"
            )
        return self._chain_client
    
    @property
    def chains(self) -> AsyncChainsClient:
        """
        Get the full chains client for accessing other chains.
        
        While the spine chain is the primary focus, agents may need to interact
        with other chains for coordination, shared state, or communication.
        
        Returns:
            AsyncChainsClient for accessing any chain
            
        Raises:
            RuntimeError: If called before connecting (use async context manager)
        """
        if not self._session_active or not self._chains_client:
            raise RuntimeError(
                "Player not connected. Use 'async with player:' or call 'await player._ensure_connected()'"
            )
        return self._chains_client
    
    async def integrate(
        self,
        stop_on_missing: bool = True,
        poll_interval: float = 1.0,
        adaptive_batching: bool = True,
        initial_batch_size: int = 10,
        max_batch_size: int = 1000,
        max_queue_depth: int = 100,
        yield_metadata: bool = False
    ) -> AsyncGenerator[str, None]:
        """
        Async generator that yields strings based on the spine chain configuration.
        
        This is the core integration method that allows agents to process their spine
        chain data in real-time or batch mode. It automatically handles chain reading,
        adaptive batching for performance, and intelligent data processing.
        
        Args:
            stop_on_missing: If True, stop when reaching the end of existing blocks.
                           If False, continue polling for new blocks indefinitely.
            poll_interval: Seconds to wait between polls when stop_on_missing=False
            adaptive_batching: Whether to use adaptive batch sizing for performance
            initial_batch_size: Starting batch size for block fetching
            max_batch_size: Maximum batch size allowed
            max_queue_depth: Target queue depth for adaptive batching
            yield_metadata: If True, yield metadata about the integration process
            
        Yields:
            String representations of spine chain data and integration status
            
        Example:
            # Process existing spine data and stop
            async for message in player.integrate(stop_on_missing=True):
                await handle_decision(message)
            
            # Live agent that continuously processes new spine data
            async for message in player.integrate(stop_on_missing=False):
                result = await process_realtime(message)
                await player.append_to_spine(["response", result])
        """
        await self._ensure_connected()
        
        if yield_metadata:
            yield f"Player '{self.user.username}' initializing integration with spine chain '{self.chain}'"
            yield f"Connected to API at: {self.url}"
        
        try:
            # Check if spine chain exists and get initial metadata
            chain_exists = await self.spine.exists()
            if not chain_exists:
                if yield_metadata:
                    yield f"Spine chain '{self.chain}' does not exist yet"
                
                if stop_on_missing:
                    if yield_metadata:
                        yield "Integration complete - no spine chain found"
                    return
                else:
                    if yield_metadata:
                        yield "Waiting for spine chain to be created..."
            else:
                metadata = await self.spine.get_metadata()
                if yield_metadata:
                    yield f"Spine chain found with {metadata.block_count} blocks"
                    yield f"Chain head: {metadata.current_head}, tail: {metadata.current_tail}"
            
            # Main integration loop - iterate through spine blocks
            block_count = 0
            last_block_time = None
            
            async for block in self.spine.iterate_blocks(
                stop_on_missing=stop_on_missing,
                poll_interval=poll_interval,
                adaptive_batching=adaptive_batching,
                initial_batch_size=initial_batch_size,
                max_batch_size=max_batch_size,
                max_queue_depth=max_queue_depth
            ):
                block_count += 1
                
                # Extract meaningful data from the block
                block_data = self._process_spine_block(block)
                
                # Execute migration hooks before yielding
                if block_data:
                    await self._execute_migrate_hooks(block_data)
                
                # Yield the processed block data
                if block_data:
                    yield block_data
                
                # Optionally yield metadata about processing
                if yield_metadata:
                    time_since_last = None
                    if last_block_time:
                        time_since_last = (block.created_at - last_block_time).total_seconds()
                    
                    metadata_msg = f"[Block {block.block_idx}] Processed at {block.created_at}"
                    if time_since_last:
                        metadata_msg += f" (+{time_since_last:.1f}s from previous)"
                    yield metadata_msg
                
                last_block_time = block.created_at
                
                # Yield progress updates periodically
                if yield_metadata and block_count % 100 == 0:
                    yield f"Integration progress: {block_count} blocks processed"
            
            if yield_metadata:
                yield f"Integration completed after processing {block_count} blocks"
                
        except Exception as e:
            error_msg = f"Integration error: {e}"
            if yield_metadata:
                yield error_msg
            else:
                # Always yield errors even if metadata is disabled
                yield error_msg
    
    def _process_spine_block(self, block: BlockResponse) -> Optional[str]:
        """
        Process a spine block and convert it to a meaningful string for the agent.
        
        This method intelligently parses different types of spine block content
        and converts them into actionable strings for agent processing. It handles
        various common patterns like commands, messages, and structured data.
        
        Args:
            block: BlockResponse from the spine chain
            
        Returns:
            Processed string representation, or None to skip this block
            
        Note:
            This method can be overridden in subclasses to handle custom
            spine data formats specific to particular agent types.
        """
        if not block.values:
            return None
        
        # Handle different types of spine block content
        try:
            # If block contains a single string, use it directly
            if len(block.values) == 1 and isinstance(block.values[0], str):
                return block.values[0]
            
            # If block contains structured data, format it appropriately
            elif len(block.values) > 1:
                # Check if it's a command-style block [action, *args]
                if isinstance(block.values[0], str) and block.values[0].startswith('/'):
                    command = block.values[0]
                    args = block.values[1:] if len(block.values) > 1 else []
                    return f"{command} {' '.join(str(arg) for arg in args)}".strip()
                
                # Check if it's a message-style block [sender, message, *metadata]
                elif len(block.values) >= 2:
                    sender = str(block.values[0])
                    message = str(block.values[1])
                    if len(block.values) > 2:
                        metadata = block.values[2:]
                        return f"[{sender}] {message} {metadata}"
                    else:
                        return f"[{sender}] {message}"
                
                # Generic multi-value block
                else:
                    return " | ".join(str(value) for value in block.values)
            
            # Handle special data types
            else:
                value = block.values[0]
                
                # Handle dict/object data
                if isinstance(value, dict):
                    if 'message' in value:
                        return str(value['message'])
                    elif 'content' in value:
                        return str(value['content'])
                    elif 'data' in value:
                        return str(value['data'])
                    else:
                        return str(value)
                
                # Handle list data
                elif isinstance(value, list):
                    return " | ".join(str(item) for item in value)
                
                # Default to string conversion
                else:
                    return str(value)
                    
        except Exception as e:
            # If processing fails, return a safe representation
            return f"[Block {block.block_idx}] Processing error: {e} | Raw: {block.values}"
    
    # Spine Chain Operations
    
    async def append_to_spine(self, values: List[Any]) -> BlockResponse:
        """
        Append data to the spine chain.
        
        This is the primary way for agents to add new decisions, state changes,
        or responses to their spine chain.
        
        Args:
            values: List of values to append as a new block
            
        Returns:
            BlockResponse for the created block
            
        Example:
            await player.append_to_spine(["decision", "move_forward"])
            await player.append_to_spine(["/command", "set_mode", "exploration"])
        """
        await self._ensure_connected()
        return await self.spine.append(values)
    
    async def prepend_to_spine(self, values: List[Any]) -> BlockResponse:
        """
        Prepend data to the spine chain (insert at head).
        
        Useful for high-priority messages or corrections that need to be
        processed before other pending spine data.
        
        Args:
            values: List of values to prepend as a new block
            
        Returns:
            BlockResponse for the created block
            
        Example:
            await player.prepend_to_spine(["urgent", "emergency_stop"])
            await player.prepend_to_spine(["priority", "override_current_task"])
        """
        await self._ensure_connected()
        return await self.spine.prepend(values)
    
    async def get_spine_metadata(self):
        """
        Get metadata about the spine chain.
        
        Returns information about the spine chain including block count,
        head/tail positions, creation time, etc.
        
        Returns:
            ChainMetadata object with spine chain information
        """
        await self._ensure_connected()
        return await self.spine.get_metadata()
    
    async def get_spine_recent_blocks(self, count: int = 10) -> List[BlockResponse]:
        """
        Get recent blocks from the spine chain.
        
        Useful for getting context about recent agent decisions or states
        without processing the entire spine.
        
        Args:
            count: Number of recent blocks to retrieve (max 100)
            
        Returns:
            List of recent BlockResponse objects
        """
        await self._ensure_connected()
        return await self.spine.get_recent_blocks(count)
    
    async def get_spine_current_state(self) -> Optional[str]:
        """
        Get the most recent spine block as the current agent state.
        
        Returns:
            String representation of the most recent spine block, or None if empty
        """
        try:
            last_block = await self.spine.get_last_block()
            return self._process_spine_block(last_block)
        except Exception:
            return None
    
    def migrate_hook(self, target_players: List['Player'], priority: int = 0):
        """
        Decorator for registering migration logic to transfer blocks to multiple players.
        
        Migrate hooks process spine blocks and can transform them into zero or more
        blocks that get added to all specified target players' spines concurrently.
        This enables sophisticated inter-agent communication, data transformation, 
        and workflow coordination with multiple recipients.
        
        Args:
            target_players: List of Player instances to receive the migrated blocks
            priority: Hook priority (lower numbers run first)
            
        Returns:
            Decorator function that registers the migration hook
            
        Example:
            source = Player(url, Login("source", "pass"), "source-spine")
            target1 = Player(url, Login("target1", "pass"), "target1-spine")
            target2 = Player(url, Login("target2", "pass"), "target2-spine")
            
            # Single target (list with one element)
            @source.migrate_hook([target1], priority=1)
            async def migrate_to_one(block_data: str) -> List[str]:
                return [f"processed: {block_data}"]
            
            # Multiple targets (concurrent delivery to all)
            @source.migrate_hook([target1, target2], priority=2)
            async def broadcast_to_many(block_data: str) -> List[str]:
                if "broadcast:" in block_data:
                    return [f"received: {block_data}"]
                return []
        """
        def decorator(func):
            if not asyncio.iscoroutinefunction(func):
                raise TypeError(f"Migrate hook '{func.__name__}' must be async")
            
            if not isinstance(target_players, list):
                raise TypeError("target_players must be a list of Player instances")
            
            if not target_players:
                raise ValueError("target_players list cannot be empty")
            
            # Validate all targets are Player instances
            for i, target in enumerate(target_players):
                if not isinstance(target, Player):
                    raise TypeError(f"target_players[{i}] must be a Player instance, got {type(target).__name__}")
            
            # Validate the hook signature
            import inspect
            sig = inspect.signature(func)
            if len(sig.parameters) != 1:
                raise TypeError(f"Migrate hook '{func.__name__}' must accept exactly one parameter (block_data: str)")
            
            self._migrate_hooks.append((target_players, priority, func))
            self._migrate_hooks.sort(key=lambda x: x[1])  # Sort by priority
            return func
        return decorator
    
    async def _execute_migrate_hooks(self, block_data: str):
        """
        Execute all registered migrate hooks and send results to target players concurrently.
        
        Args:
            block_data: The processed string representation of a spine block
        """
        # Collect all migration tasks to run concurrently
        migration_tasks = []
        
        for target_players, priority, hook in self._migrate_hooks:
            try:
                # Execute the migration hook
                migration_results = await hook(block_data)
                
                # Ensure we have a list of strings
                if migration_results is None:
                    continue
                elif isinstance(migration_results, str):
                    migration_results = [migration_results]
                elif not isinstance(migration_results, list):
                    migration_results = list(migration_results)
                
                # Create migration tasks for each target player
                for target_player in target_players:
                    for result in migration_results:
                        if isinstance(result, str) and result.strip():
                            migration_tasks.append(
                                self._migrate_to_player(target_player, result)
                            )
                            
            except Exception as e:
                # Log error but continue with other hooks
                target_names = [p.chain for p in target_players]
                print(f"Migration hook error for targets {target_names}: {e}")
                continue
        
        # Execute all migrations concurrently
        if migration_tasks:
            await asyncio.gather(*migration_tasks, return_exceptions=True)
    
    async def _migrate_to_player(self, target_player: 'Player', result: str):
        """
        Migrate a single result to a target player.
        
        Args:
            target_player: The target Player instance
            result: The migration result string to send
        """
        try:
            # Ensure target player is connected
            await target_player._ensure_connected()
            # Add the migrated block to target's spine
            await target_player.append_to_spine(["migrated", result])
        except Exception as e:
            print(f"Failed to migrate to '{target_player.chain}': {e}")
    
    async def create_side_chain(self, chain_name: str) -> ChainClient:
        """
        Create and return a focused client for a side chain.
        
        Agents may need additional chains for specific purposes like
        communication logs, temporary calculations, or coordination data.
        
        Args:
            chain_name: Name of the side chain to create/access
            
        Returns:
            ChainClient focused on the specified side chain
        """
        await self._ensure_connected()
        return self.chains.chain(chain_name)
    
    async def communicate_with_agent(self, target_chain: str, message: List[Any]) -> BlockResponse:
        """
        Send a message to another agent's chain.
        
        This provides a simple interface for inter-agent communication
        via the chains infrastructure.
        
        Args:
            target_chain: Name of the target agent's chain
            message: Message data to send
            
        Returns:
            BlockResponse for the sent message
        """
        await self._ensure_connected()
        target = self.chains.chain(target_chain)
        return await target.append(message)
    
    def __repr__(self) -> str:
        """String representation of the Player."""
        return f"Player(url='{self.url}', user='{self.user.username}', chain='{self.chain}')"


# Specialized Player Classes

class AutonomousPlayer(Player):
    """
    Player subclass for autonomous agents that make independent decisions.
    
    This class extends Player with additional methods and behaviors specific
    to autonomous agents that operate independently with minimal supervision.
    
    Use the @decision_hook decorator to define custom decision-making logic:
    
    Example:
        agent = AutonomousPlayer(url, user, chain)
        
        @agent.decision_hook()
        async def my_decision_logic(context: str) -> Optional[str]:
            if "emergency" in context:
                return "activate_safety_protocol"
            return "continue_mission"
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._decision_hooks = []
        # Note: _migrate_hooks is inherited from parent Player class
    
    def decision_hook(self, priority: int = 0):
        """
        Decorator for registering autonomous decision-making logic.
        
        Args:
            priority: Hook priority (lower numbers run first)
            
        Example:
            @agent.decision_hook(priority=1)
            async def emergency_decisions(context: str) -> Optional[str]:
                if "emergency" in context.lower():
                    return "emergency_stop"
                return None
        """
        def decorator(func):
            if not asyncio.iscoroutinefunction(func):
                raise TypeError(f"Decision hook '{func.__name__}' must be async")
            
            self._decision_hooks.append((priority, func))
            self._decision_hooks.sort(key=lambda x: x[0])  # Sort by priority
            return func
        return decorator
    
    async def autonomous_loop(
        self,
        decision_interval: float = 1.0,
        max_idle_time: float = 30.0
    ) -> AsyncGenerator[str, None]:
        """
        Run an autonomous decision-making loop based on spine integration.
        
        Args:
            decision_interval: Minimum time between autonomous decisions
            max_idle_time: Maximum time to wait for spine updates before making autonomous decision
            
        Yields:
            Decision messages and autonomous actions
        """
        last_decision_time = datetime.now()
        
        async for message in self.integrate(stop_on_missing=False, poll_interval=decision_interval):
            current_time = datetime.now()
            time_since_last = (current_time - last_decision_time).total_seconds()
            
            # Process spine message
            yield f"Processing: {message}"
            
            # Make autonomous decisions based on time and content
            if time_since_last >= max_idle_time or self._should_make_decision(message):
                decision = await self._execute_decision_hooks(message)
                if decision:
                    await self.append_to_spine(["autonomous_decision", decision])
                    yield f"Decision: {decision}"
                    last_decision_time = current_time
    
    def _should_make_decision(self, message: str) -> bool:
        """Determine if a message triggers an autonomous decision."""
        triggers = ["/command", "urgent", "decision_required", "autonomous"]
        return any(trigger in message.lower() for trigger in triggers)
    
    async def _execute_decision_hooks(self, context: str) -> Optional[str]:
        """
        Execute all registered decision hooks in priority order.
        Returns the first non-None result.
        """
        # Execute registered hooks first
        for priority, hook in self._decision_hooks:
            try:
                result = await hook(context)
                if result is not None:
                    return result
            except Exception as e:
                # Log error but continue with other hooks
                print(f"Decision hook error: {e}")
                continue
        
        # Fallback to default decision logic if no hooks return a result
        return await self._default_autonomous_decision(context)
    
    async def _default_autonomous_decision(self, context: str) -> Optional[str]:
        """
        Default autonomous decision logic used when no hooks provide a result.
        Can be overridden in subclasses.
        """
        if "urgent" in context.lower():
            return "emergency_protocol_activated"
        elif "/command" in context:
            return "command_acknowledged"
        else:
            return "continue_current_task"


class ReactivePlayer(Player):
    """
    Player subclass for reactive agents that respond to external stimuli.
    
    Reactive players focus on responding to spine changes rather than
    making autonomous decisions.
    
    Use the @response_hook decorator to define custom response logic:
    
    Example:
        agent = ReactivePlayer(url, user, chain)
        
        @agent.response_hook()
        async def my_response_logic(stimulus: str) -> Optional[str]:
            if "hello" in stimulus.lower():
                return "Hello! How can I help you?"
            return None
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._response_hooks = []
        # Note: _migrate_hooks is inherited from parent Player class
    
    def response_hook(self, priority: int = 0):
        """
        Decorator for registering response generation logic.
        
        Args:
            priority: Hook priority (lower numbers run first)
            
        Example:
            @agent.response_hook(priority=1)
            async def urgent_responses(stimulus: str) -> Optional[str]:
                if "urgent" in stimulus.lower():
                    return "URGENT: Immediate attention required"
                return None
        """
        def decorator(func):
            if not asyncio.iscoroutinefunction(func):
                raise TypeError(f"Response hook '{func.__name__}' must be async")
            
            self._response_hooks.append((priority, func))
            self._response_hooks.sort(key=lambda x: x[0])  # Sort by priority
            return func
        return decorator
    
    async def reactive_loop(self, response_delay: float = 0.1) -> AsyncGenerator[str, None]:
        """
        Run a reactive loop that responds to each spine message.
        
        Args:
            response_delay: Delay between receiving message and responding
            
        Yields:
            Response messages
        """
        async for message in self.integrate(stop_on_missing=False):
            # Small delay to allow for processing
            await asyncio.sleep(response_delay)
            
            # Generate response using hooks
            response = await self._execute_response_hooks(message)
            if response:
                await self.append_to_spine(["response", response])
                yield f"Responded to '{message}' with: {response}"
    
    async def _execute_response_hooks(self, stimulus: str) -> Optional[str]:
        """
        Execute all registered response hooks in priority order.
        Returns the first non-None result.
        """
        # Execute registered hooks first
        for priority, hook in self._response_hooks:
            try:
                result = await hook(stimulus)
                if result is not None:
                    return result
            except Exception as e:
                # Log error but continue with other hooks
                print(f"Response hook error: {e}")
                continue
        
        # Fallback to default response logic if no hooks return a result
        return await self._default_response(stimulus)
    
    async def _default_response(self, stimulus: str) -> Optional[str]:
        """
        Default response logic used when no hooks provide a result.
        Can be overridden in subclasses.
        """
        return f"acknowledged: {stimulus}"


# Example usage and testing functions

async def example_basic_player():
    """Example demonstrating basic Player usage."""
    
    player = Player(
        url="https://api.example.com",
        user=Login("agent_user", "secure_password"),
        chain="agent-decision-spine"
    )
    
    async with player:
        # Initialize spine with some data
        await player.append_to_spine(["init", "Agent starting up"])
        await player.append_to_spine(["/command", "set_mode", "autonomous"])
        await player.append_to_spine(["system", "Ready for integration"])
        
        # Process existing spine data
        print("=== Processing spine data ===")
        async for message in player.integrate(stop_on_missing=True):
            print(f"Agent processed: {message}")


async def example_autonomous_agent():
    """Example demonstrating AutonomousPlayer with decision hooks."""
    
    agent = AutonomousPlayer(
        url="https://api.example.com",
        user=Login("autonomous_agent", "password"),
        chain="autonomous-spine"
    )
    
    # Register custom decision-making logic using hooks
    @agent.decision_hook(priority=1)
    async def emergency_handler(context: str) -> Optional[str]:
        """High-priority hook for emergency situations."""
        if "emergency" in context.lower() or "urgent" in context.lower():
            return "EMERGENCY_PROTOCOL_ACTIVATED"
        return None
    
    @agent.decision_hook(priority=5)
    async def command_handler(context: str) -> Optional[str]:
        """Handle command-style messages."""
        if context.startswith("/"):
            command_parts = context.split()
            if len(command_parts) >= 2:
                return f"EXECUTING_{command_parts[1].upper()}"
        return None
    
    @agent.decision_hook(priority=10)
    async def learning_handler(context: str) -> Optional[str]:
        """Low-priority hook for learning and adaptation."""
        if "learn" in context.lower() or "adapt" in context.lower():
            return "initiating_learning_sequence"
        return None
    
    async with agent:
        # Seed with different types of messages
        await agent.append_to_spine(["status", "operational"])
        await agent.append_to_spine(["urgent", "system_overload_detected"])
        await agent.append_to_spine(["/command", "patrol", "sector_7"])
        await agent.append_to_spine(["message", "learn from recent interactions"])
        
        # Run autonomous loop
        print("=== Autonomous agent with decision hooks ===")
        count = 0
        async for decision in agent.autonomous_loop(decision_interval=0.5):
            print(f"Autonomous: {decision}")
            count += 1
            if count >= 8:  # Limit for example
                break


async def example_reactive_agent():
    """Example demonstrating ReactivePlayer with response hooks."""
    
    agent = ReactivePlayer(
        url="https://api.example.com",
        user=Login("reactive_agent", "password"),
        chain="reactive-spine"
    )
    
    # Register custom response logic using hooks
    @agent.response_hook(priority=1)
    async def greeting_handler(stimulus: str) -> Optional[str]:
        """High-priority hook for greetings."""
        greetings = ["hello", "hi", "hey", "greetings"]
        if any(greeting in stimulus.lower() for greeting in greetings):
            return "Hello! I'm ready to assist you."
        return None
    
    @agent.response_hook(priority=2)
    async def question_handler(stimulus: str) -> Optional[str]:
        """Handle questions."""
        if "?" in stimulus or stimulus.lower().startswith(("what", "how", "why", "when", "where")):
            return "Let me process that question and get back to you."
        return None
    
    @agent.response_hook(priority=5)
    async def sentiment_handler(stimulus: str) -> Optional[str]:
        """Respond based on sentiment."""
        if "thank" in stimulus.lower():
            return "You're welcome! Happy to help."
        elif "sorry" in stimulus.lower() or "apologize" in stimulus.lower():
            return "No problem at all!"
        return None
    
    async with agent:
        # Seed with different types of stimuli
        await agent.append_to_spine(["user", "Hello there!"])
        await agent.append_to_spine(["user", "What is the current status?"])
        await agent.append_to_spine(["user", "Thank you for your help"])
        await agent.append_to_spine(["system", "Processing request..."])
        
        print("=== Reactive agent with response hooks ===")
        count = 0
        async for response in agent.reactive_loop(response_delay=0.2):
            print(f"Reactive: {response}")
            count += 1
            if count >= 6:  # Limit for example
                break


async def example_multi_agent_with_hooks():
    """Example demonstrating multi-agent interaction with custom hooks."""
    
    # Create a coordinator agent with decision hooks
    coordinator = AutonomousPlayer(
        url="https://api.example.com",
        user=Login("coordinator", "coord_pass"),
        chain="coordinator-spine"
    )
    
    # Create a worker agent with response hooks
    worker = ReactivePlayer(
        url="https://api.example.com",
        user=Login("worker", "worker_pass"),
        chain="worker-spine"
    )
    
    # Coordinator decision logic
    @coordinator.decision_hook(priority=1)
    async def task_assignment(context: str) -> Optional[str]:
        if "task_available" in context:
            return "assign_task_to_worker"
        elif "worker_idle" in context:
            return "find_new_tasks"
        return None
    
    # Worker response logic
    @worker.response_hook(priority=1)
    async def task_acceptance(stimulus: str) -> Optional[str]:
        if "assign_task" in stimulus:
            return "task_accepted_starting_work"
        elif "status_check" in stimulus:
            return "currently_working_on_assigned_task"
        return None
    
    async with coordinator, worker:
        # Simulate workflow
        await coordinator.append_to_spine(["system", "task_available: process_data"])
        await worker.append_to_spine(["coordinator", "assign_task: process_data"])
        
        print("=== Multi-agent coordination with hooks ===")
        
        # Process coordinator decisions
        print("Coordinator decisions:")
        async for decision in coordinator.integrate(stop_on_missing=True):
            print(f"  Coord: {decision}")
        
        # Process worker responses
        print("Worker responses:")
        async for response in worker.integrate(stop_on_missing=True):
            print(f"  Worker: {response}")


async def example_hook_priority_demonstration():
    """Example demonstrating how hook priorities work."""
    
    agent = AutonomousPlayer(
        url="https://api.example.com",
        user=Login("priority_agent", "password"),
        chain="priority-spine"
    )
    
    # Register hooks with different priorities
    @agent.decision_hook(priority=10)  # Low priority (runs last)
    async def default_handler(context: str) -> Optional[str]:
        return f"default_response_to: {context[:20]}..."
    
    @agent.decision_hook(priority=1)   # High priority (runs first)
    async def critical_handler(context: str) -> Optional[str]:
        if "critical" in context.lower():
            return "CRITICAL_ALERT_PROCESSED"
        return None  # Let other hooks handle it
    
    @agent.decision_hook(priority=5)   # Medium priority
    async def moderate_handler(context: str) -> Optional[str]:
        if "moderate" in context.lower():
            return "moderate_action_taken"
        return None
    
    async with agent:
        # Test different priority scenarios
        await agent.append_to_spine(["alert", "critical system failure"])
        await agent.append_to_spine(["info", "moderate priority update"])
        await agent.append_to_spine(["log", "regular status message"])
        
        print("=== Hook priority demonstration ===")
        async for decision in agent.integrate(stop_on_missing=True):
            print(f"Priority result: {decision}")


async def example_migration_hooks():
    """Example demonstrating migration hooks for inter-agent communication."""
    
    # Create source and target agents
    source_agent = Player(
        url="https://api.example.com",
        user=Login("source_agent", "source_pass"),
        chain="source-spine"
    )
    
    target_agent = Player(
        url="https://api.example.com",
        user=Login("target_agent", "target_pass"),
        chain="target-spine"
    )
    
    coordinator_agent = Player(
        url="https://api.example.com",
        user=Login("coordinator", "coord_pass"),
        chain="coordinator-spine"
    )
    
    # Set up migration hooks
    
    @source_agent.migrate_hook([target_agent], priority=1)
    async def migrate_commands(block_data: str) -> List[str]:
        """Migrate command blocks to target agent."""
        if block_data.startswith("/"):
            command_parts = block_data.split()
            if len(command_parts) >= 2:
                return [f"execute_{command_parts[1]}: {' '.join(command_parts[2:])}"]
        return []
    
    @source_agent.migrate_hook([coordinator_agent], priority=2)
    async def report_to_coordinator(block_data: str) -> List[str]:
        """Report important events to coordinator."""
        important_keywords = ["urgent", "error", "complete", "failed"]
        if any(keyword in block_data.lower() for keyword in important_keywords):
            return [f"source_report: {block_data}"]
        return []
    
    @source_agent.migrate_hook([target_agent], priority=5)
    async def transform_data(block_data: str) -> List[str]:
        """Transform data blocks for target consumption."""
        if "data:" in block_data.lower():
            # Transform data format
            data_part = block_data.split("data:", 1)[1].strip()
            return [f"processed_data: {data_part.upper()}"]
        return []
    
    # Multiple targets from one hook
    @source_agent.migrate_hook([coordinator_agent], priority=3)
    async def broadcast_status(block_data: str) -> List[str]:
        """Broadcast status updates."""
        if "status:" in block_data.lower():
            status = block_data.split("status:", 1)[1].strip()
            return [
                f"status_update: {status}",
                f"timestamp: {datetime.now().isoformat()}"
            ]
        return []
    
    async with source_agent, target_agent, coordinator_agent:
        # Add various types of data to source spine
        await source_agent.append_to_spine(["/command", "patrol", "sector_7"])
        await source_agent.append_to_spine(["urgent", "system_overload_detected"])
        await source_agent.append_to_spine(["data:", "sensor_readings_12345"])
        await source_agent.append_to_spine(["status:", "operational_ready"])
        await source_agent.append_to_spine(["info", "routine_maintenance_log"])
        
        print("=== Migration hooks demonstration ===")
        print("Source agent processing (triggers migrations):")
        
        # Process source spine - this triggers migration hooks
        async for message in source_agent.integrate(stop_on_missing=True):
            print(f"  Source: {message}")
        
        # Check what was migrated to target agent
        print("\nTarget agent received:")
        async for message in target_agent.integrate(stop_on_missing=True):
            print(f"  Target: {message}")
        
        # Check what was migrated to coordinator
        print("\nCoordinator received:")
        async for message in coordinator_agent.integrate(stop_on_missing=True):
            print(f"  Coordinator: {message}")


async def example_workflow_with_migration():
    """Example of a complete workflow using migration hooks."""
    
    # Create a processing pipeline with multiple agents
    input_agent = Player(
        url="https://api.example.com",
        user=Login("input_processor", "pass1"),
        chain="input-spine"
    )
    
    analysis_agent = Player(
        url="https://api.example.com",
        user=Login("analyzer", "pass2"),
        chain="analysis-spine"
    )
    
    output_agent = Player(
        url="https://api.example.com",
        user=Login("output_generator", "pass3"),
        chain="output-spine"
    )
    
    # Set up processing pipeline
    @input_agent.migrate_hook([analysis_agent], priority=1)
    async def prepare_for_analysis(block_data: str) -> List[str]:
        """Prepare input data for analysis."""
        if "raw_data:" in block_data:
            data = block_data.split("raw_data:", 1)[1].strip()
            # Split data into chunks for analysis
            chunks = [chunk.strip() for chunk in data.split(",")]
            return [f"analyze_chunk: {chunk}" for chunk in chunks if chunk]
        return []
    
    @analysis_agent.migrate_hook([output_agent], priority=1)
    async def generate_results(block_data: str) -> List[str]:
        """Generate output from analysis results."""
        if "analyze_chunk:" in block_data:
            chunk = block_data.split("analyze_chunk:", 1)[1].strip()
            # Simulate analysis result
            result = f"processed_{chunk}_result"
            return [f"output: {result}"]
        return []
    
    @analysis_agent.migrate_hook([input_agent], priority=2)
    async def feedback_loop(block_data: str) -> List[str]:
        """Send feedback to input processor."""
        if "analyze_chunk:" in block_data:
            return ["feedback: chunk_received_for_analysis"]
        return []
    
    async with input_agent, analysis_agent, output_agent:
        # Start the workflow
        await input_agent.append_to_spine(["raw_data:", "item1, item2, item3, item4"])
        await input_agent.append_to_spine(["raw_data:", "batch_a, batch_b"])
        
        print("=== Workflow with migration pipeline ===")
        
        # Process input (triggers analysis)
        print("Input processing:")
        async for message in input_agent.integrate(stop_on_missing=True):
            print(f"  Input: {message}")
        
        # Process analysis (triggers output)
        print("\nAnalysis processing:")
        async for message in analysis_agent.integrate(stop_on_missing=True):
            print(f"  Analysis: {message}")
        
        # Check final output
        print("\nOutput generation:")
        async for message in output_agent.integrate(stop_on_missing=True):
            print(f"  Output: {message}")


async def example_conditional_migration():
    """Example showing conditional migration based on content patterns."""
    
    central_hub = Player(
        url="https://api.example.com",
        user=Login("central_hub", "hub_pass"),
        chain="hub-spine"
    )
    
    security_agent = Player(
        url="https://api.example.com",
        user=Login("security", "sec_pass"),
        chain="security-spine"
    )
    
    maintenance_agent = Player(
        url="https://api.example.com",
        user=Login("maintenance", "maint_pass"),
        chain="maintenance-spine"
    )
    
    analytics_agent = Player(
        url="https://api.example.com",
        user=Login("analytics", "analytics_pass"),
        chain="analytics-spine"
    )
    
    # Conditional routing based on content
    @central_hub.migrate_hook([security_agent], priority=1)
    async def route_security_events(block_data: str) -> List[str]:
        """Route security-related events."""
        security_keywords = ["security", "breach", "unauthorized", "alert", "threat"]
        if any(keyword in block_data.lower() for keyword in security_keywords):
            return [f"security_event: {block_data}", f"priority: high"]
        return []
    
    @central_hub.migrate_hook([maintenance_agent], priority=2)
    async def route_maintenance_events(block_data: str) -> List[str]:
        """Route maintenance-related events."""
        maintenance_keywords = ["maintenance", "repair", "error", "fault", "service"]
        if any(keyword in block_data.lower() for keyword in maintenance_keywords):
            return [f"maintenance_request: {block_data}"]
        return []
    
    @central_hub.migrate_hook([analytics_agent], priority=3)
    async def route_analytics_data(block_data: str) -> List[str]:
        """Route all events to analytics for tracking."""
        # Analytics gets everything for tracking purposes
        return [f"track_event: {block_data}", f"timestamp: {datetime.now().isoformat()}"]
        return []
    
    @central_hub.migrate_hook(analytics_agent, priority=3)
    async def route_analytics_data(block_data: str) -> List[str]:
        """Route all events to analytics for tracking."""
        # Analytics gets everything for tracking purposes
        return [f"track_event: {block_data}", f"timestamp: {datetime.now().isoformat()}"]
    
    async with central_hub, security_agent, maintenance_agent, analytics_agent:
        # Send various types of events to central hub
        await central_hub.append_to_spine(["alert", "unauthorized_access_detected"])
        await central_hub.append_to_spine(["system", "maintenance_required_sector_3"])
        await central_hub.append_to_spine(["info", "routine_status_update"])
        await central_hub.append_to_spine(["security", "threat_level_elevated"])
        await central_hub.append_to_spine(["error", "service_fault_in_module_7"])
        
        print("=== Conditional migration routing ===")
        
        # Process central hub (triggers routing)
        print("Central hub processing:")
        async for message in central_hub.integrate(stop_on_missing=True):
            print(f"  Hub: {message}")
        
        # Check what each specialist agent received
        print("\nSecurity agent received:")
        async for message in security_agent.integrate(stop_on_missing=True):
            print(f"  Security: {message}")
        
        print("\nMaintenance agent received:")
        async for message in maintenance_agent.integrate(stop_on_missing=True):
            print(f"  Maintenance: {message}")
        
        print("\nAnalytics agent received:")
        async for message in analytics_agent.integrate(stop_on_missing=True):
            print(f"  Analytics: {message}")


async def example_multi_target_migration():
    """Example demonstrating migration to multiple targets concurrently."""
    
    # Create source and multiple target agents
    broadcaster = Player(
        url="https://api.example.com",
        user=Login("broadcaster", "broadcast_pass"),
        chain="broadcast-spine"
    )
    
    listener1 = Player(
        url="https://api.example.com",
        user=Login("listener1", "listen1_pass"),
        chain="listener1-spine"
    )
    
    listener2 = Player(
        url="https://api.example.com",
        user=Login("listener2", "listen2_pass"),
        chain="listener2-spine"
    )
    
    listener3 = Player(
        url="https://api.example.com",
        user=Login("listener3", "listen3_pass"),
        chain="listener3-spine"
    )
    
    # Single target migration
    @broadcaster.migrate_hook([listener1], priority=1)
    async def private_message(block_data: str) -> List[str]:
        """Send private messages to listener1 only."""
        if "private:" in block_data.lower():
            content = block_data.split("private:", 1)[1].strip()
            return [f"confidential: {content}"]
        return []
    
    # Multi-target broadcasting
    @broadcaster.migrate_hook([listener1, listener2, listener3], priority=2)
    async def broadcast_announcements(block_data: str) -> List[str]:
        """Broadcast announcements to all listeners."""
        if "announce:" in block_data.lower():
            content = block_data.split("announce:", 1)[1].strip()
            return [f"announcement: {content}", f"from: broadcaster"]
        return []
    
    # Selective multi-target migration
    @broadcaster.migrate_hook([listener2, listener3], priority=3)
    async def group_updates(block_data: str) -> List[str]:
        """Send group updates to listeners 2 and 3."""
        if "group:" in block_data.lower():
            content = block_data.split("group:", 1)[1].strip()
            return [f"group_update: {content}"]
        return []
    
    # All targets with different content per recipient
    @broadcaster.migrate_hook([listener1, listener2, listener3], priority=4)
    async def status_updates(block_data: str) -> List[str]:
        """Send status updates to all listeners."""
        if "status:" in block_data.lower():
            status = block_data.split("status:", 1)[1].strip()
            return [
                f"status_report: {status}",
                f"timestamp: {datetime.now().isoformat()}",
                "priority: normal"
            ]
        return []
    
    async with broadcaster, listener1, listener2, listener3:
        # Send various types of messages
        await broadcaster.append_to_spine(["private:", "secret_mission_details"])
        await broadcaster.append_to_spine(["announce:", "system_maintenance_at_midnight"])
        await broadcaster.append_to_spine(["group:", "team_alpha_beta_coordination"])
        await broadcaster.append_to_spine(["status:", "all_systems_operational"])
        await broadcaster.append_to_spine(["info", "regular_log_entry"])  # No migration
        
        print("=== Multi-target migration demonstration ===")
        print("Broadcaster processing (triggers concurrent migrations):")
        
        # Process broadcaster spine - this triggers all migrations concurrently
        async for message in broadcaster.integrate(stop_on_missing=True):
            print(f"  Broadcaster: {message}")
        
        # Check what each listener received
        print("\nListener 1 received:")
        async for message in listener1.integrate(stop_on_missing=True):
            print(f"  Listener1: {message}")
        
        print("\nListener 2 received:")
        async for message in listener2.integrate(stop_on_missing=True):
            print(f"  Listener2: {message}")
        
        print("\nListener 3 received:")
        async for message in listener3.integrate(stop_on_missing=True):
            print(f"  Listener3: {message}")


async def example_concurrent_pipeline():
    """Example showing concurrent processing pipeline with multiple outputs."""
    
    # Create a data processing pipeline
    data_source = Player(
        url="https://api.example.com",
        user=Login("data_source", "source_pass"),
        chain="data-source-spine"
    )
    
    processor_a = Player(
        url="https://api.example.com",
        user=Login("processor_a", "proc_a_pass"),
        chain="processor-a-spine"
    )
    
    processor_b = Player(
        url="https://api.example.com",
        user=Login("processor_b", "proc_b_pass"),
        chain="processor-b-spine"
    )
    
    aggregator = Player(
        url="https://api.example.com",
        user=Login("aggregator", "agg_pass"),
        chain="aggregator-spine"
    )
    
    monitor = Player(
        url="https://api.example.com",
        user=Login("monitor", "monitor_pass"),
        chain="monitor-spine"
    )
    
    # Fan-out: Send data to multiple processors concurrently
    @data_source.migrate_hook([processor_a, processor_b], priority=1)
    async def distribute_processing(block_data: str) -> List[str]:
        """Distribute data to processors for parallel processing."""
        if "process:" in block_data.lower():
            data = block_data.split("process:", 1)[1].strip()
            return [f"task_data: {data}", f"batch_id: {datetime.now().timestamp()}"]
        return []
    
    # Fan-in: Send results to aggregator and monitor
    @processor_a.migrate_hook([aggregator, monitor], priority=1)
    async def processor_a_results(block_data: str) -> List[str]:
        """Send processor A results to aggregator and monitor."""
        if "task_data:" in block_data:
            data = block_data.split("task_data:", 1)[1].strip()
            return [f"result_a: processed_{data}_by_a"]
        return []
    
    @processor_b.migrate_hook([aggregator, monitor], priority=1)
    async def processor_b_results(block_data: str) -> List[str]:
        """Send processor B results to aggregator and monitor."""
        if "task_data:" in block_data:
            data = block_data.split("task_data:", 1)[1].strip()
            return [f"result_b: processed_{data}_by_b"]
        return []
    
    # Monitor everything
    @data_source.migrate_hook([monitor], priority=10)
    async def monitor_source_activity(block_data: str) -> List[str]:
        """Monitor data source activity."""
        return [f"monitor_log: source_{block_data}"]
    
    @processor_a.migrate_hook([monitor], priority=10)
    async def monitor_processor_a_activity(block_data: str) -> List[str]:
        """Monitor processor A activity."""
        return [f"monitor_log: proc_a_{block_data}"]
    
    @processor_b.migrate_hook([monitor], priority=10)
    async def monitor_processor_b_activity(block_data: str) -> List[str]:
        """Monitor processor B activity."""
        return [f"monitor_log: proc_b_{block_data}"]
    
    @aggregator.migrate_hook([monitor], priority=10)
    async def monitor_aggregator_activity(block_data: str) -> List[str]:
        """Monitor aggregator activity."""
        return [f"monitor_log: agg_{block_data}"]
    
    async with data_source, processor_a, processor_b, aggregator, monitor:
        # Start data processing
        await data_source.append_to_spine(["process:", "dataset_alpha"])
        await data_source.append_to_spine(["process:", "dataset_beta"])
        await data_source.append_to_spine(["process:", "dataset_gamma"])
        
        print("=== Concurrent processing pipeline ===")
        
        # Simulate processing delay and concurrent execution
        await asyncio.sleep(0.1)
        
        # Process all stages
        print("Data source processing:")
        async for message in data_source.integrate(stop_on_missing=True):
            print(f"  Source: {message}")
        
        print("\nProcessor A processing:")
        async for message in processor_a.integrate(stop_on_missing=True):
            print(f"  ProcA: {message}")
        
        print("\nProcessor B processing:")
        async for message in processor_b.integrate(stop_on_missing=True):
            print(f"  ProcB: {message}")
        
        print("\nAggregator results:")
        async for message in aggregator.integrate(stop_on_missing=True):
            print(f"  Aggregator: {message}")
        
        print("\nMonitor logs:")
        async for message in monitor.integrate(stop_on_missing=True):
            print(f"  Monitor: {message}")


async def example_selective_broadcasting():
    """Example showing selective broadcasting based on content and context."""
    
    # Create a notification system
    notification_hub = Player(
        url="https://api.example.com",
        user=Login("notification_hub", "hub_pass"),
        chain="notification-hub-spine"
    )
    
    admin_console = Player(
        url="https://api.example.com",
        user=Login("admin", "admin_pass"),
        chain="admin-console-spine"
    )
    
    user_dashboard = Player(
        url="https://api.example.com",
        user=Login("user_dash", "user_pass"),
        chain="user-dashboard-spine"
    )
    
    mobile_app = Player(
        url="https://api.example.com",
        user=Login("mobile", "mobile_pass"),
        chain="mobile-app-spine"
    )
    
    analytics = Player(
        url="https://api.example.com",
        user=Login("analytics", "analytics_pass"),
        chain="analytics-spine"
    )
    
    # Admin-only notifications
    @notification_hub.migrate_hook([admin_console], priority=1)
    async def admin_notifications(block_data: str) -> List[str]:
        """Send admin-only notifications."""
        admin_keywords = ["admin:", "critical:", "system:", "error:"]
        if any(keyword in block_data.lower() for keyword in admin_keywords):
            return [f"admin_alert: {block_data}", "level: administrative"]
        return []
    
    # User-facing notifications (dashboard and mobile)
    @notification_hub.migrate_hook([user_dashboard, mobile_app], priority=2)
    async def user_notifications(block_data: str) -> List[str]:
        """Send user-facing notifications to both dashboard and mobile."""
        user_keywords = ["user:", "update:", "feature:", "maintenance:"]
        if any(keyword in block_data.lower() for keyword in user_keywords):
            content = block_data.split(":", 1)[1].strip() if ":" in block_data else block_data
            return [f"user_notification: {content}"]
        return []
    
    # Mobile-only push notifications
    @notification_hub.migrate_hook([mobile_app], priority=3)
    async def push_notifications(block_data: str) -> List[str]:
        """Send push notifications to mobile only."""
        if "push:" in block_data.lower():
            content = block_data.split("push:", 1)[1].strip()
            return [f"push_notification: {content}", "immediate: true"]
        return []
    
    # Analytics gets everything for tracking
    @notification_hub.migrate_hook([analytics], priority=10)
    async def track_all_notifications(block_data: str) -> List[str]:
        """Track all notifications for analytics."""
        return [
            f"notification_event: {block_data}",
            f"timestamp: {datetime.now().isoformat()}",
            "tracked: true"
        ]
    
    # Broadcast critical alerts to everyone
    @notification_hub.migrate_hook([admin_console, user_dashboard, mobile_app], priority=0)
    async def critical_broadcast(block_data: str) -> List[str]:
        """Broadcast critical alerts to all interfaces."""
        if "critical:" in block_data.lower():
            content = block_data.split("critical:", 1)[1].strip()
            return [
                f"CRITICAL_ALERT: {content}",
                f"severity: high",
                f"broadcast_time: {datetime.now().isoformat()}"
            ]
        return []
    
    async with notification_hub, admin_console, user_dashboard, mobile_app, analytics:
        # Send various types of notifications
        await notification_hub.append_to_spine(["critical:", "security_breach_detected"])
        await notification_hub.append_to_spine(["admin:", "database_backup_completed"])
        await notification_hub.append_to_spine(["user:", "new_feature_available"])
        await notification_hub.append_to_spine(["push:", "friend_request_received"])
        await notification_hub.append_to_spine(["maintenance:", "scheduled_downtime_tonight"])
        await notification_hub.append_to_spine(["system:", "performance_optimization_complete"])
        
        print("=== Selective broadcasting demonstration ===")
        print("Notification hub processing:")
        
        # Process notification hub
        async for message in notification_hub.integrate(stop_on_missing=True):
            print(f"  Hub: {message}")
        
        # Check what each interface received
        print("\nAdmin console received:")
        async for message in admin_console.integrate(stop_on_missing=True):
            print(f"  Admin: {message}")
        
        print("\nUser dashboard received:")
        async for message in user_dashboard.integrate(stop_on_missing=True):
            print(f"  Dashboard: {message}")
        
        print("\nMobile app received:")
        async for message in mobile_app.integrate(stop_on_missing=True):
            print(f"  Mobile: {message}")
        
        print("\nAnalytics received:")
        async for message in analytics.integrate(stop_on_missing=True):
            print(f"  Analytics: {message}")


if __name__ == "__main__":
    # Run examples
    print("Running multi-target migration example...")
    asyncio.run(example_multi_target_migration())
    
    print("\nRunning concurrent pipeline example...")
    asyncio.run(example_concurrent_pipeline())
    
    print("\nRunning selective broadcasting example...")
    asyncio.run(example_selective_broadcasting())


async def example_multi_agent_communication():
    """Example demonstrating multi-agent communication."""
    
    agent_a = Player(
        url="https://api.example.com",
        user=Login("agent_a", "pass_a"),
        chain="agent-a-spine"
    )
    
    agent_b = Player(
        url="https://api.example.com", 
        user=Login("agent_b", "pass_b"),
        chain="agent-b-spine"
    )
    
    async with agent_a, agent_b:
        # Agent A sends message to Agent B
        await agent_a.communicate_with_agent("agent-b-spine", ["greeting", "Hello Agent B"])
        
        # Agent B processes its spine and responds
        async for message in agent_b.integrate(stop_on_missing=True):
            print(f"Agent B received: {message}")
            if "greeting" in message:
                await agent_b.communicate_with_agent("agent-a-spine", ["response", "Hello Agent A"])
        
        # Agent A sees the response
        async for message in agent_a.integrate(stop_on_missing=True):
            print(f"Agent A received: {message}")


if __name__ == "__main__":
    # Run examples
    asyncio.run(example_basic_player())