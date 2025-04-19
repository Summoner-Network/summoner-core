from __future__ import annotations
from pydantic import BaseModel
import asyncio
from typing import Optional, Callable, Any, Iterable, Union
from contextlib import asynccontextmanager
from openai import RateLimitError as OpenAIRateLimitError
from fireworks.client.error import RateLimitError as FireworksRateLimitError
from fireworks.client.error import BadGatewayError as FireworksBadGatewayError
import httpx
from tqdm import tqdm

import black


def identical_application(arg):
    """
    A simple function that returns the argument it receives.

    Args:
    - arg (Any): The input argument.

    Returns:
    - Any: The same input argument.
    """
    return arg


class AsyncExecutionError(Exception):
    """
    Custom exception for errors that occur during asynchronous execution.

    This is intended to provide more specific error handling for async-related failures.
    """

    pass


class LocalAgent:
    """
    Represents a local agent that manages data flow and behavior definitions
    for an asynchronous system.

    Attributes:
    - name (str): The name of the local agent.
    - agent_dict (dict[str, dict]): A dictionary managing the agent's state,
      including requests and application logic.
    """

    def __init__(self, agent_name: str, agent_dict: dict[str, dict]):
        """
        Initializes the LocalAgent instance with a name and agent dictionary.

        Args:
        - agent_name (str): The name of the local agent.
        - agent_dict (dict[str, dict]): A dictionary structure containing agent
          configurations and state.
        """
        self.name = agent_name  # Name of the agent for identification
        self.agent_dict = agent_dict  # Dictionary holding the agent's state

    def take_from_input(
        self, use_as: str, var_name: Optional[str] = None, default: Any = None
    ):
        """
        Configures the agent to use a value from the input dictionary.

        Args:
        - use_as (str): The key that this value will be assigned to in the agent's request.
        - var_name (Optional[str]): The variable name in the input to reference.
          If None, references the model directly.
        - default (Any): A default value to use if the variable is not present.

        Raises:
        - ValueError: If `var_name` or `use_as` is invalid, or if the variable
          name is already assigned in the agent's request.
        """
        # Determine the reference key based on var_name
        if var_name is None:
            reference = f"<model>"  # Represents a direct reference to the model
        elif isinstance(var_name, str):
            reference = f"<dict>.{var_name}"  # Represents a key in the input dictionary
        else:
            raise ValueError(
                f"\033[90mLocalAgent {self.name} (take_from_input): variable name must be None or string, got {type(var_name)}.\033[0m"
            )

        # Check if the reference already exists in the agent's request dictionary
        if reference in self.agent_dict["request"]:
            raise ValueError(
                f"\033[90mLocalAgent {self.name} (take_from_input): variable name {var_name} already assigned.\033[0m"
            )

        # Ensure `use_as` is a valid key
        if not isinstance(use_as, str) or "." in use_as:
            raise ValueError(
                f"\033[90mLocalAgent {self.name} (take_from_input): invalid use_as value.\033[0m"
            )

        # Add the reference to the request dictionary with associated metadata
        self.agent_dict["request"].setdefault(
            reference, {"use_as": use_as, "default": default}
        )

    def take_from_agent(
        self,
        use_as: str,
        agent_name: str,
        var_name: Optional[str] = None,
        default: Any = None,
    ):
        """
        Configures the agent to use a value from another agent's output.

        Args:
        - use_as (str): The key that this value will be assigned to in the agent's request.
        - agent_name (str): The name of the agent to reference.
        - var_name (Optional[str]): The variable name in the other agent's output to reference.
          If None, references the model directly.
        - default (Any): A default value to use if the variable is not present.

        Raises:
        - ValueError: If `var_name` or `use_as` is invalid, or if the variable
          name is already assigned in the agent's request.
        """
        # Determine the reference key based on agent_name and var_name
        if var_name is None:
            reference = f"{agent_name}.<model>"  # Represents a reference to another agent's model
        elif isinstance(var_name, str):
            reference = f"{agent_name}.<dict>.{var_name}"  # Represents a key in another agent's output dictionary
        else:
            raise ValueError(
                f"\033[90mLocalAgent {self.name} (take_from_agent): variable name must be None or string, got {type(var_name)}.\033[0m"
            )

        # Check if the reference already exists in the agent's request dictionary
        if reference in self.agent_dict["request"]:
            raise ValueError(
                f"\033[90mLocalAgent {self.name} (take_from_agent): variable name {reference} already assigned.\033[0m"
            )

        # Ensure `use_as` is a valid key
        if not isinstance(use_as, str) or "." in use_as:
            raise ValueError(
                f"\033[90mLocalAgent {self.name} (take_from_agent): invalid use_as value.\033[0m"
            )

        # Add the reference to the request dictionary with associated metadata
        self.agent_dict["request"].setdefault(
            reference, {"use_as": use_as, "default": default}
        )

    def behave_as(self, function: Callable):
        """
        Defines the behavior of the agent using a given function.

        Args:
        - function (Callable): The function that determines the agent's behavior
          when processing input or generating output.

        Example:
        >>> agent = LocalAgent("example", {})
        >>> agent.behave_as(lambda x: x * 2)  # Agent will double its input
        """
        self.agent_dict["apply"] = function  # Assigns the function to the "apply" key


# Class representing a network of interconnected agents, handling concurrent operations.
class AgentGraph:
    """
    AgentGraph is a framework for managing and executing a network of interdependent
    agents in a structured and efficient manner. It enables defining agent workflows,
    resolving dependencies, and controlling execution order.

    Core Responsibilities:
    - Represents agents and their interdependencies within a directed acyclic graph (DAG).
    - Manages input data and intermediate states using a shared "tape".
    - Supports concurrency control for agent execution with optional limits on
      the number of simultaneous tasks.
    - Provides utilities for dependency resolution, level-based scheduling, and
      output formatting.
    - Includes methods for debugging and visualization, such as ASCII-based
      dependency graphs.

    Attributes:
    - name (str): The name of the agent network, primarily for identification purposes.
    - tape (list): A shared data structure to hold inputs, intermediate results,
      and states during agent execution.
    - agents (dict[str, Any]): A dictionary of all agents, where keys are agent names
      and values store agent configurations, including inputs and outputs.
    - scheduler (Optional[Iterable]): Defines the execution order of agents, usually
      based on dependency resolution.
    - output_format (dict): Specifies the expected format of the output
    - semaphore (asyncio.Semaphore): Controls the maximum number of concurrent tasks
      allowed during execution, ensuring resource limits are respected.
    - default_timeout (float): The default timeout (in seconds) for individual
      agent executions.
    - _tasks (set[asyncio.Task]): A private attribute tracking currently active tasks
      for better error handling and cancellation support.

    Use Case:
    Ideal for orchestrating complex workflows where tasks depend on the outputs of
    other tasks, such as in multi-agent systems, ETL pipelines, or distributed processing.
    """

    def __init__(
        self,
        agentgraph_name: str = "<no name given>",
        max_concurrent: Optional[int] = None,
        default_timeout: float = 60.0,
    ):
        """
        Initializes an AgentGraph instance to manage a network of agents.

        Parameters:
        - agentgraph_name (str): Name of the agent graph.
        - max_concurrent (int, optional): Maximum number of concurrent agent executions.
        - default_timeout (float): Default timeout for each agent execution in seconds.
        """
        self.name = agentgraph_name
        self.tape: dict[int, list[dict]] = (
            {}
        )  # Placeholder for inputs or intermediate results.
        self.agents: dict[str, Any] = {}  # Dictionary storing all defined agents.
        self.scheduler: Optional[dict[int, list]] = (
            None  # Determines agent execution order.
        )
        self.output_format: dict[str, Any] = {
            "request": {},
            "model": None,
        }  # Expected output format.
        self.semaphore = asyncio.Semaphore(
            max_concurrent if max_concurrent else float("inf")
        )  # Concurrency control.
        self.default_timeout = default_timeout  # Task timeout.
        self._tasks: set[asyncio.Task] = set()  # Set to track active tasks.

        # New attributes for concurrency handling
        self.tape_lock = asyncio.Lock()
        self.active_batches: set[int] = set()
        self.batch_results: dict[int, Any] = {}
        self.cleanup_enabled = True

    @asynccontextmanager
    async def _managed_task(self, coro):
        """
        Context manager for creating and cleaning up async tasks.

        Parameters:
        - coro (coroutine): The coroutine to manage as a task.
        """
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        try:
            yield task
        finally:
            self._tasks.remove(task)

    async def _cancel_tasks(self):
        """
        Cancels all currently running tasks.
        """
        for task in self._tasks:
            if not task.done():
                task.cancel()

        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    def define(self, agent_name: str):
        """
        Defines a new agent in the graph.

        Parameters:
        - agent_name (str): Name of the new agent.

        Returns:
        - LocalAgent: An interface for configuring the agent's behavior and requests.
        """
        if agent_name in self.agents:
            raise ValueError(f"\033[90mAgent {agent_name} already defined.\033[0m")

        agent_dict = {"request": {}, "apply": identical_application}
        self.agents[agent_name] = agent_dict
        return LocalAgent(agent_name=agent_name, agent_dict=agent_dict)

    def compose_output_with(
        self,
        use_as: str,
        agent_name: Optional[str] = None,
        var_name: Optional[str] = None,
        default: Any = None,
    ):
        """
        Configures the composition of the graph's output based on agent variables.

        Parameters:
        - use_as (str): Name of the output attribute.
        - agent_name (str, optional): Name of the agent providing the output.
        - var_name (str, optional): Specific variable to use from the agent's output.
        - default (Any): Default value if the variable is missing.
        """
        if agent_name is None:
            reference = f"<model>" if var_name is None else f"<dict>.{var_name}"
        elif isinstance(agent_name, str):
            reference = (
                f"{agent_name}.<model>"
                if var_name is None
                else f"{agent_name}.<dict>.{var_name}"
            )
        else:
            raise ValueError(
                f"\033[90mAgentGraph '{self.name}' (compose_output_with): invalid agent_name type.\033[0m"
            )

        if not isinstance(self.output_format["request"], dict):
            raise ValueError(
                f"\033[90mAgentGraph '{self.name}' (compose_output_with): invalid output format.\033[0m"
            )

        if reference in self.output_format["request"]:
            raise ValueError(
                f"\033[90mAgentGraph '{self.name}' (compose_output_with): reference {reference} already assigned.\033[0m"
            )

        if not isinstance(use_as, str) or "." in use_as:
            raise ValueError(
                f"\033[90mAgentGraph '{self.name}' (compose_output_with): invalid use_as value.\033[0m"
            )

        self.output_format["request"][reference] = {
            "use_as": use_as,
            "default": default,
        }

    def define_output_as(self, model):
        """
        Sets the model type for validating the graph's final output.

        Parameters:
        - model (BaseModel): A Pydantic model class defining the output schema.
        """
        if not isinstance(model, type(BaseModel)):
            raise ValueError(
                f"\033[90mAgentGraph '{self.name}' (define_output_as): model must be BaseModel instance.\033[0m"
            )

        if not isinstance(self.output_format["request"], dict):
            raise ValueError(
                f"\033[90mAgentGraph '{self.name}' (define_output_as): invalid output format.\033[0m"
            )

        validity = []
        for k, v in self.output_format["request"].items():
            attribute = v["use_as"]
            valid = attribute in model.__dict__["__annotations__"]
            if not valid:
                print(
                    f"\033[93m[Warning] AgentGraph '{self.name}' (define_output_as): model missing attribute '{attribute}'.\033[0m"
                )
            validity.append(valid)

        if all(validity):
            self.output_format["model"] = model
        else:
            raise ValueError(
                f"\033[90mAgentGraph '{self.name}' (define_output_as): inconsistent model attributes.\033[0m"
            )

    async def get(
        self,
        request: dict,
        batch_address: Optional[int] = None,
        tape_entry: Optional[dict] = None,
    ) -> Union[dict, list]:
        """
        #     Retrieves specific values from the tape based on a request format. Can process
        #     either a single tape entry or all entries.

        #     Parameters:
        #         request (dict): A mapping of keys to the desired output format.
        #         tape_entry (dict, optional): A single tape entry to process. If not provided,
        #                                     processes all entries.

        #     Returns:
        #         dict or list: Extracted values in the requested format.
        #"""

        async with self.tape_lock:

            if tape_entry is not None:
                return {
                    v["use_as"]: tape_entry.get(k, v["default"])
                    for k, v in request.items()
                }
            elif batch_address is not None:
                return [
                    {v["use_as"]: x.get(k, v["default"]) for k, v in request.items()}
                    for x in self.tape[batch_address]
                ]
            else:
                raise ValueError(
                    "\033[90mInvalid operation: either 'batch_address' or 'tape_entry' must be specified for 'get' method.\033[0m"
                )

    async def _update_entry(self, entry: dict, agent_name: str, item):
        """
        Updates a single tape entry with data from an agent.

        Parameters:
            entry (dict): The tape entry to update.
            agent_name (str): The name of the agent producing the data.
            item (dict or BaseModel): The data to be added to the entry.

        Raises:
            ValueError: If the data item type is unsupported or invalid.
            RateLimitError: If the data item indicates a rate limit issue.
            ConnectError: If the data item indicates a connection error.
        """
        # Check if the item is a rate limit error (Fireworks or OpenAI), or is a connection error
        if isinstance(
            item,
            (
                FireworksRateLimitError,
                OpenAIRateLimitError,
                FireworksBadGatewayError,
                httpx.ConnectError,
                httpx.RemoteProtocolError,
            ),
        ):
            # Raise the rate limit error, or the ConnectError, to be caught and handled by the caller
            raise item

        # Process the item if it's not a rate limit error or connection error
        if isinstance(item, dict):
            # Update entry with dictionary data
            entry.update(
                {
                    f"{agent_name}.<model>": item,
                    **{f"{agent_name}.<dict>.{k}": v for k, v in item.items()},
                }
            )
        elif isinstance(item, BaseModel):
            # Update entry with BaseModel data
            entry.update(
                {
                    f"{agent_name}.<model>": item,
                    **{
                        f"{agent_name}.<dict>.{k}": v
                        for k, v in item.model_dump().items()
                    },
                }
            )
        else:
            raise ValueError(
                f"\033[90mInvalid item type: {type(item)}, {str(item)}.\033[0m"
            )

    async def update(
        self,
        agent_name: str,
        items: list,
        batch_address: int,
        input_index: Optional[int] = None,
    ):
        """
        Updates the tape with new data produced by an agent. Supports updating either a
        single entry or multiple entries.

        Parameters:
            agent_name (str): The name of the agent producing the new data.
            items (list): A list of data items to update the tape with.
            input_index (int, optional): The index of the specific tape entry to update.
                                        If None, updates all entries.

        Raises:
            RateLimitError: If any data item indicates a rate limit issue.
            ConnectError: If any data item indicates a connection issue.
        """
        save_index = input_index
        async with self.tape_lock:

            try:
                if input_index is not None:
                    # Update single entry
                    await self._update_entry(
                        self.tape[batch_address][input_index], agent_name, items[0]
                    )
                else:
                    # Update multiple entries
                    for i, item in enumerate(items):
                        save_index = i
                        await self._update_entry(
                            self.tape[batch_address][i], agent_name, item
                        )
            except (FireworksRateLimitError, OpenAIRateLimitError) as e:
                print(
                    f"\033[91mRate limit error encountered during update for index {save_index} on batch {batch_address}: {e}. This will be handled during retry.\033[0m"
                )
                raise e
            except (
                httpx.ConnectError,
                httpx.RemoteProtocolError,
                FireworksBadGatewayError,
            ) as ce:
                print(
                    f"\033[91mCommunication error encountered during update for index {save_index} on batch {batch_address}: {ce}. This includes connection failures, protocol issues, or server-side gateway errors and will be handled during retry.\033[0m"
                )
                raise ce
            except Exception as e:
                print(f"\033[91mError while updating specific tape entry: \033[0m{e}")
                raise

    async def _run_agent(
        self, agent_name: str, batch_address: int, input_index: Optional[int] = None
    ) -> None:
        """
        Executes an agent's logic with retry and timeout handling for timeout, rate limit, and connection errors.

        Parameters:
        - agent_name (str): Name of the agent to execute.
        - input_index (int, optional): Index of the input data for this execution.
        """

        # Acquire the semaphore to ensure limited concurrent access to this method.
        async with self.semaphore:
            agent_dict = self.agents[agent_name]
            agent_request = agent_dict["request"]

            # Fetch parameters depending on input_index
            if input_index is not None:
                params = [
                    await self.get(
                        agent_request, tape_entry=self.tape[batch_address][input_index]
                    )
                ]
            else:
                params = await self.get(agent_request, batch_address=batch_address)

            stop_retry_timeout: bool = False
            retry_factors: list[float] = [
                1,
                1.5,
                2,
                5,
                10,
                50,
            ]  # Retry times for timeout errors
            rate_limit_waits: list[float] = [
                40,
                60,
                80,
                100,
                120,
                240,
                300,
                400,
            ]  # Wait times for rate limit errors
            connect_error_retries: list[float] = [
                5,
                10,
                20,
                40,
                60,
                80,
                100,
            ]  # Wait times for connection errors

            for retry_factor in retry_factors:
                if retry_factor != 1:
                    print(
                        f"\033[93mRetrying for {retry_factor} times the default_timeout...\033[0m"
                    )
                try:
                    # Timeout logic for retry attempts
                    async with asyncio.timeout(
                        int(self.default_timeout * retry_factor)
                    ):
                        if asyncio.iscoroutinefunction(agent_dict["apply"]):
                            outputs = await agent_dict["apply"](params)
                        else:
                            outputs = await asyncio.to_thread(
                                agent_dict["apply"], params
                            )

                        # Handle updating the results after receiving outputs
                        try:
                            if input_index is not None:
                                await self.update(
                                    agent_name, outputs, batch_address, input_index
                                )
                            else:
                                await self.update(agent_name, outputs, batch_address)

                            # time.sleep(3)

                            break  # Exit retry loop if update is successful

                        # Handle rate limit errors with retries
                        except (FireworksRateLimitError, OpenAIRateLimitError) as e:
                            print(
                                f"\033[95mRate limit error encountered: {e}. Retrying after backoff.\033[0m"
                            )
                            for wait_time in rate_limit_waits:

                                print(
                                    f"\033[93mRetrying after {wait_time} seconds...\033[0m"
                                )
                                await asyncio.sleep(wait_time)  # Wait before retrying

                                # Retry after waiting
                                if asyncio.iscoroutinefunction(agent_dict["apply"]):
                                    outputs = await agent_dict["apply"](params)
                                else:
                                    outputs = await asyncio.to_thread(
                                        agent_dict["apply"], params
                                    )
                                try:
                                    if input_index is not None:
                                        await self.update(
                                            agent_name,
                                            outputs,
                                            batch_address,
                                            input_index,
                                        )
                                    else:
                                        await self.update(
                                            agent_name, outputs, batch_address
                                        )
                                    break  # Exit retry loop if successful
                                except (FireworksRateLimitError, OpenAIRateLimitError):
                                    continue  # Continue retrying if rate limit persists
                            else:
                                # If rate limit errors continue after retries, raise an error
                                raise AsyncExecutionError(
                                    f"\033[90mAgent {agent_name} failed due to repeated rate limit errors.\033[0m"
                                )

                        # Handle connection errors with retries
                        except (
                            httpx.ConnectError,
                            httpx.RemoteProtocolError,
                            FireworksBadGatewayError,
                        ) as ce:
                            print(
                                f"\033[95mCommunication error encountered: {ce}. Retrying after backoff.\033[0m"
                            )
                            for wait_time in connect_error_retries:
                                print(
                                    f"\033[93mRetrying after {wait_time} seconds...\033[0m"
                                )
                                await asyncio.sleep(wait_time)  # Wait before retrying
                                # Retry after waiting
                                if asyncio.iscoroutinefunction(agent_dict["apply"]):
                                    outputs = await agent_dict["apply"](params)
                                else:
                                    outputs = await asyncio.to_thread(
                                        agent_dict["apply"], params
                                    )
                                try:
                                    if input_index is not None:
                                        await self.update(
                                            agent_name,
                                            outputs,
                                            batch_address,
                                            input_index,
                                        )
                                    else:
                                        await self.update(
                                            agent_name, outputs, batch_address
                                        )
                                    break  # Exit retry loop if successful
                                except (
                                    httpx.ConnectError,
                                    httpx.RemoteProtocolError,
                                    FireworksBadGatewayError,
                                ):
                                    continue  # Continue retrying if connection error persists
                            else:
                                # If connection errors persist after retries, raise an error
                                raise AsyncExecutionError(
                                    f"\033[90mAgent {agent_name} failed due to repeated communication errors to server.\033[0m"
                                )

                        # Handle any other exceptions that are raised
                        except Exception as e:
                            # Extract error details, if any, from the exception
                            error_details = getattr(e, "args", [None])[
                                0
                            ]  # Get the first argument if provided
                            server_disconnected = (
                                "Server disconnected without sending a response"
                                in str(e)
                            )

                            # Check if the error is related to server overload
                            if (
                                isinstance(error_details, dict)
                                and "error" in error_details
                            ) or server_disconnected:

                                error_type = error_details["error"].get(
                                    "type", "Unknown"
                                )
                                error_message = error_details["error"].get(
                                    "message", "No message provided"
                                )
                                print(
                                    f"\033[95mAsync processing failed: {error_type} - {error_message}\033[0m"
                                )

                                # If it's an overload error, handle it specifically
                                if (
                                    "overloaded" in error_message.lower()
                                    or "gateway" in error_message.lower()
                                    or "unavailable" in error_message.lower()
                                    or server_disconnected
                                ):
                                    print(
                                        f"\033[95mServer unavailability error detected during {agent_name} execution: {error_message}\033[0m"
                                    )

                                    for wait_time in connect_error_retries:
                                        print(
                                            f"\033[93mRetrying after {wait_time} seconds...\033[0m"
                                        )
                                        await asyncio.sleep(
                                            wait_time
                                        )  # Wait before retrying
                                        # Retry after waiting
                                        if asyncio.iscoroutinefunction(
                                            agent_dict["apply"]
                                        ):
                                            outputs = await agent_dict["apply"](params)
                                        else:
                                            outputs = await asyncio.to_thread(
                                                agent_dict["apply"], params
                                            )
                                        try:
                                            if input_index is not None:
                                                await self.update(
                                                    agent_name,
                                                    outputs,
                                                    batch_address,
                                                    input_index,
                                                )
                                            else:
                                                await self.update(
                                                    agent_name, outputs, batch_address
                                                )
                                            break  # Exit retry loop if successful

                                        except Exception as e:
                                            # Extract error details, if any, from the exception
                                            error_details = getattr(e, "args", [None])[
                                                0
                                            ]  # Get the first argument if provided
                                            server_disconnected = (
                                                "Server disconnected without sending a response"
                                                in str(e)
                                            )

                                            # Check if the error is related to server overload
                                            if (
                                                isinstance(error_details, dict)
                                                and "error" in error_details
                                            ) or server_disconnected:

                                                error_message = error_details[
                                                    "error"
                                                ].get("message", "No message provided")

                                                if (
                                                    "overloaded" in error_message.lower() 
                                                    or "gateway" in error_message.lower() 
                                                    or "unavailable" in error_message.lower()
                                                    or server_disconnected
                                                ):
                                                    continue

                                                else:
                                                    raise

                                            else:
                                                raise

                                    else:
                                        # If server availability errors persist after retries, raise an error
                                        raise AsyncExecutionError(
                                            f"\033[90mAgent {agent_name} failed due to server unavailability.\033[0m"
                                        )

                                else:
                                    raise

                            else:
                                raise

                # Handle timeout errors with retries
                except asyncio.TimeoutError:
                    stop_retry_timeout = retry_factor == retry_factors[-1]
                    continue  # Retry on timeout

                # Handle any other exceptions that are raised
                except Exception as e:
                    raise AsyncExecutionError(
                        f"\033[90mAgent {agent_name} execution failed: {str(e)}\033[0m"
                    ) from e

            # If the final retry for timeout is exhausted, raise an error
            if stop_retry_timeout:
                raise AsyncExecutionError(
                    f"\033[90mAgent {agent_name} execution timed out after {self.default_timeout * retry_factors[-1]} seconds.\033[0m"
                )

    async def _process_horizontal(self, batch_address: int):
        """
        Processes agents horizontally level by level, where all agents in the same level
        are executed concurrently. This method ensures proper error handling by managing
        tasks and canceling them if exceptions occur.

        Workflow:
            1. Iterate through the levels of the scheduler in sorted order.
            2. For each level, execute all agents concurrently using asyncio tasks.
            3. If any exception occurs, cancel all tasks and raise an AsyncExecutionError.

        Raises:
            AsyncExecutionError: If any agent processing fails at a given level.
        """
        for level in sorted(self.scheduler.keys()):
            agents_at_level = self.scheduler[level]
            tasks = []

            try:
                for agent_name in agents_at_level:
                    async with self._managed_task(
                        self._run_agent(agent_name, batch_address)
                    ) as task:
                        tasks.append(task)

                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Check for exceptions
                for result in results:
                    if isinstance(result, Exception):
                        raise result

            except Exception as e:
                await self._cancel_tasks()
                raise AsyncExecutionError(
                    f"\033[90mHorizontal processing failed at level {level}: {str(e)}\033[0m"
                ) from e

    async def _process_vertical(self, batch_address: int):
        """
        Processes agents vertically, where each input (from the tape) is passed through
        all levels sequentially. Agents in the same level are executed concurrently
        for each input.

        Workflow:
            1. Iterate over all inputs from the tape.
            2. For each input, iterate through the levels in the scheduler.
            3. Execute agents concurrently at each level for the current input.
            4. If any exception occurs, raise an AsyncExecutionError.

        Raises:
            AsyncExecutionError: If any agent processing fails for a given input or level.
        """
        try:
            for input_index in range(len(self.tape[batch_address])):
                for level in sorted(self.scheduler.keys()):
                    agents_at_level = self.scheduler[level]
                    tasks = []

                    for agent_name in agents_at_level:
                        async with self._managed_task(
                            self._run_agent(
                                agent_name, batch_address, input_index=input_index
                            )
                        ) as task:
                            tasks.append(task)

                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    # Check for exceptions
                    for result in results:
                        if isinstance(result, Exception):
                            raise result

        except Exception as e:
            await self._cancel_tasks()
            raise AsyncExecutionError(
                f"\033[90mVertical processing failed: {str(e)}\033[0m"
            ) from e

    async def _process_async(
        self, batch_address: int, process_mode: str = "horizontal"
    ):
        """
        Main async processing entry point for the graph. Initializes the tape, schedules
        agents, and processes the graph based on the given mode (horizontal or vertical).

        Parameters:
            base_inputs (list): The initial inputs for the graph.
            process_mode (str): Mode of processing, either "horizontal" or "vertical".

        Returns:
            list: Processed outputs from the agent graph.

        Raises:
            AsyncExecutionError: If processing fails in either mode.
        """
        try:

            # if self.scheduler is None:
            #     self.scheduler = self._schedule_agents()

            # self.tape = self._initialize_tape(base_inputs)

            if process_mode == "horizontal":
                await self._process_horizontal(batch_address)

            elif process_mode == "vertical":
                await self._process_vertical(batch_address)

            else:
                raise ValueError(
                    "\033[90mInvalid process_mode. Use 'horizontal' or 'vertical'.\033[0m"
                )

            return await self.return_output(batch_address)

        except Exception as e:
            await self._cancel_tasks()
            raise AsyncExecutionError(
                f"\033[90mAsync processing failed: {str(e)}\033[0m"
            ) from e

    # Helper methods remain largely unchanged but with improved error handling
    async def _initialize_tape(
        self, batches: list[list[Union[dict, BaseModel]]]
    ) -> None:
        """
        Initializes a tape structure for processing batches of input data. Converts
        input dictionaries or BaseModel instances into a consistent dictionary format.

        Parameters:
            batches (list[list]): A list of batches, where each batch is a list of inputs
                                (dictionaries or BaseModel instances).

        Raises:
            ValueError: If an input is not a dictionary or a BaseModel instance.
        """
        async with self.tape_lock:
            print(
                f"\033[95mPreparing AgentGraph '{self.name}' to be run on {len(batches)} batches\033[0m"
            )

            self.tape.clear()

            for addr, batch in enumerate(batches):

                self.tape[addr] = []

                for base_input in batch:
                    if isinstance(base_input, dict):
                        self.tape[addr].append(
                            {
                                "<model>": base_input,
                                **{f"<dict>.{k}": v for k, v in base_input.items()},
                            }
                        )
                    elif isinstance(base_input, BaseModel):
                        self.tape[addr].append(
                            {
                                "<model>": base_input,
                                **{
                                    f"<dict>.{k}": v
                                    for k, v in base_input.model_dump().items()
                                },
                            }
                        )
                    else:
                        raise ValueError(
                            f"\033[90mInvalid input type: {type(base_input)}\033[0m"
                        )

    async def _cleanup_batch(self, batch_address: int) -> None:
        """
        Safely cleans up processed batch data.
        """
        if not self.cleanup_enabled:
            return

        async with self.tape_lock:

            if batch_address in self.tape:
                del self.tape[batch_address]

            if batch_address in self.active_batches:
                self.active_batches.remove(batch_address)

    async def _combine_results(self, results: list[dict]) -> list:
        """
        Safely combines results from all batches.
        """
        combined_results = []

        for result in results:
            if result != {}:
                batch_key = next(iter(result))
                batch_data = result[batch_key]
                combined_results.extend(batch_data)
            else:
                print("\033[93mWarning: Invalid result entry encountered.\033[0m")

        return combined_results

    async def _process_async_with_batches(
        self, base_inputs: list, process_mode: str, batch_size: int
    ) -> list:
        """
        Processes inputs in batches asynchronously, maintaining concurrency while ensuring cleanup.
        Handles cases where the last batch size differs from the others.
        """
        if self.scheduler is None:
            self.scheduler = self._schedule_agents()

        # Split inputs into batches
        batches = [
            base_inputs[i : i + batch_size]
            for i in range(0, len(base_inputs), batch_size)
        ]

        # Initialize tape with all batches
        await self._initialize_tape(batches)

        async def process_and_cleanup(addr: int) -> dict:
            """
            Processes a single batch and ensures cleanup.
            """
            self.active_batches.add(addr)
            try:
                return await self._process_async(addr, process_mode)
            finally:
                await self._cleanup_batch(addr)

        try:

            all_results = await asyncio.gather(
                *[process_and_cleanup(addr) for addr in range(len(batches))]
            )

            # # Separate last batch if its size differs from the rest
            # process_last_batch_separately = (
            #     len(batches) > 1 and len(batches[-1]) != batch_size
            # )

            # # Process regular batches concurrently
            # if process_last_batch_separately:
            #     # Process all batches except the last one concurrently
            #     regular_batch_results = await asyncio.gather(
            #         *[process_and_cleanup(addr) for addr in range(len(batches) - 1)]
            #     )

            #     # Process the last batch using the same helper function
            #     last_result = await process_and_cleanup(len(batches) - 1)

            #     # Concatenate the results
            #     all_results = regular_batch_results + [last_result]

            # else:
            #     # Process all batches concurrently if they are of uniform size
            #     all_results = await asyncio.gather(
            #         *[process_and_cleanup(addr) for addr in range(len(batches))]
            #     )

            # Combine results using the helper function
            combined_results = await self._combine_results(all_results)

            return combined_results

        except Exception as e:
            print(f"\033[91mError during batch processing: {e}\033[0m")
            # Cleanup for any remaining active batches
            for addr in self.active_batches.copy():
                await self._cleanup_batch(addr)
            raise

    def __call__(
        self,
        base_inputs: list,
        process_mode: str = "horizontal",
        batch_size: Optional[int] = None,
    ) -> list:
        """
        Main entry point for executing the agent graph. Determines whether to use
        synchronous or asynchronous processing based on the context.

        Parameters:
            base_inputs (list): The initial inputs for the graph.
            process_mode (str): Mode of processing, either "horizontal" or "vertical".
            batch_size (int): Size of each input batch for asynchronous processing.

        Returns:
            list: Processed outputs from the agent graph.

        Notes:
            - If in an async context, uses the running event loop.
            - If not, creates a new event loop for processing.
        """
        if not base_inputs:
            raise ValueError("\033[90mbase_inputs cannot be empty.\033[0m")

        if batch_size is None:
            batch_size = len(base_inputs)

        try:
            # Attempt to get the currently running asyncio event loop.
            loop = asyncio.get_running_loop()

            # If we are in an async context, handle async processing by batching inputs
            print("\033[93mAsync mode: Using existing async event loop.\033[0m")
            return self._process_async_with_batches(
                base_inputs, process_mode, batch_size
            )

        except RuntimeError:
            # If no event loop is running, use asyncio.run() to run the async function
            print("\033[93mAsync mode: Creating new async event loop.\033[0m")
            return asyncio.run(
                self._process_async_with_batches(base_inputs, process_mode, batch_size)
            )

    # Dependencies and visualization methods remain unchanged
    def dependencies(self):
        """
        Constructs a dictionary of agent dependencies based on their input requirements.

        Returns:
            dict: A dictionary where keys are agent names and values are lists of agents
                they depend on.
        """
        raw_dict = {}
        for agent_name, agent_dict in self.agents.items():
            raw_dict[agent_name] = [
                k.split(".<dict>")[0] if ".<dict>" in k else k.split(".<model>")[0]
                for k in agent_dict["request"].keys()
            ]

        dependencies_dict = {}
        for agent_name, connections in raw_dict.items():
            dependencies_dict[agent_name] = [
                k for k in connections if k in self.agents.keys()
            ]

        return dependencies_dict

    @staticmethod
    def levels(dependencies_dict: dict[str, list]) -> dict[str, int]:
        """
        Assigns execution levels to agents based on their dependencies. Agents with no
        dependencies are assigned level 0.

        Parameters:
            dependencies_dict (dict): A dictionary of agent dependencies.

        Returns:
            dict: A dictionary mapping agent names to their execution levels.

        Raises:
            KeyError: If a required dependency is missing.
        """
        levels_dict = {}

        def assign_level(agent: str) -> None:
            if agent in levels_dict:
                return  # Level already assigned
            if agent not in dependencies_dict:
                raise KeyError(
                    f"\033[90mMissing definition for dependency: {agent}\033[0m"
                )
            # Recursively assign levels for dependencies
            for parent in dependencies_dict[agent]:
                assign_level(parent)
            # Compute the level as 1 + max(dependency levels)
            levels_dict[agent] = 1 + max(
                (levels_dict[parent] for parent in dependencies_dict[agent]), default=-1
            )

        for agent in dependencies_dict:
            assign_level(agent)  # Ensures all levels are computed

        return levels_dict

    @staticmethod
    def strategy(levels: dict[str, int]) -> dict[int, list]:
        """
        Groups agents into levels for execution based on their dependency levels.

        Parameters:
            levels (dict): A dictionary mapping agent names to their execution levels.

        Returns:
            dict: A dictionary mapping levels to lists of agents.
        """
        strategy_dict: dict[int, list] = {}
        for agent, level in levels.items():
            strategy_dict.setdefault(level, [])
            strategy_dict[level].append(agent)
        return strategy_dict

    async def return_output(self, batch_address: int):
        """
        Retrieves the processed output from the tape based on the defined output format.

        Returns:
            list: The final output in the desired format. If a model is specified,
                validates the output against the model.
        """
        outputs = await self.get(
            self.output_format["request"], batch_address=batch_address
        )
        model = self.output_format.get("model")

        if model is not None and isinstance(model, type(BaseModel)):
            return [model.model_validate(output) for output in outputs]

        return {batch_address: outputs}

    @staticmethod
    def plot_dependency_graph_ascii(dependencies_dict: dict):
        """
        Creates and prints an ASCII representation of the dependency graph.

        Parameters:
            dependencies_dict (dict): A dictionary of agent dependencies.
        """
        levels = AgentGraph.levels(dependencies_dict)
        strategy = AgentGraph.strategy(levels)

        # Prepare ASCII graph representation
        graph_lines = []

        for level, agents_at_level in strategy.items():
            graph_lines.append(f"Level {level}:")
            for agent in agents_at_level:
                dependencies = dependencies_dict.get(agent, [])
                dep_str = (
                    " | ".join(dependencies) if dependencies else "No dependencies"
                )
                graph_lines.append(f"  {agent}: {dep_str}")
            graph_lines.append("")

        # Print the graph in ASCII art
        print("\n".join(graph_lines))

    def _schedule_agents(self):
        """
        Generates the execution schedule for agents based on their dependencies.

        Returns:
            dict: A strategy dictionary mapping levels to lists of agents.
        """
        dependencies_dict = self.dependencies()
        levels_dict = AgentGraph.levels(dependencies_dict)
        return AgentGraph.strategy(levels_dict)


from .agents import BaseAgentModel, BaseProbAgentModel, BaseImageAgent, PDFParserAgent


def get_attributes(cls_or_model: Any) -> list[str]:
    """
    Returns a list of attribute names for a given Pydantic model or Python class.

    :param cls_or_model: The Pydantic model or Python class to inspect.
    :return: A list of attribute names as strings.
    """
    if hasattr(cls_or_model, "__annotations__"):
        # For Pydantic models and dataclasses
        return list(cls_or_model.__annotations__.keys())
    elif isinstance(cls_or_model, type):  # Check if it's a class type
        # Use vars(cls_or_model) to get __annotations__ or explicitly declared attributes
        annotations = cls_or_model.__dict__.get("__annotations__", {})
        return list(annotations.keys())
    else:
        raise TypeError(
            "\033[90mUnsupported type. Input must be a class or a Pydantic model.\033[0m"
        )


class LinearAgentGraph:
    """
    Implements a directed graph of agent interactions that pass their output to the next agent in the graph.
    """

    def __init__(
        self,
        agents: list[
            Union[BaseAgentModel, BaseProbAgentModel, BaseImageAgent, PDFParserAgent]
        ],
        input_variable_names: list[str],
        hook=None,
        max_concurrent: Optional[int] = None,
        default_timeout: float = 60.0,
    ):

        agent_names = []
        for agent in agents:
            agent_names.append(agent.name)

        agentgraph_name = "=>".join(agent_names)
        self.agentgraph = AgentGraph(
            agentgraph_name=agentgraph_name,
            max_concurrent=max_concurrent,
            default_timeout=default_timeout,
        )

        if agents != []:
            embedded_agent = self.agentgraph.define("Node0: " + agents[0].name)
            for variable_name in input_variable_names:
                embedded_agent.take_from_input(
                    use_as=variable_name, var_name=variable_name
                )
            embedded_agent.behave_as(agents[0].async_batch_api_calls)

        for i in range(1, len(agents)):
            embedded_agent = self.agentgraph.define(f"Node{i}: " + agents[i].name)
            embedded_agent.take_from_agent(
                use_as="input", agent_name=f"Node{i-1}: " + agents[i - 1].name
            )
            embedded_agent.behave_as(agents[i].async_batch_api_calls)

        for i in range(len(agents)):
            self.agentgraph.compose_output_with(
                use_as=agents[i].name, agent_name=f"Node{i}: " + agents[i].name
            )

    def __call__(self, base_inputs, process_mode="horizontal", batch_size=None):
        return self.agentgraph.__call__(
            base_inputs, process_mode=process_mode, batch_size=batch_size
        )
