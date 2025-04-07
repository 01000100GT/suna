"""
Conversation thread management system for AgentPress.

This module provides comprehensive conversation management, including:
- Thread creation and persistence
- Message handling with support for text and images
- Tool registration and execution
- LLM interaction with streaming support
- Error handling and cleanup
"""

import json
import uuid
from typing import List, Dict, Any, Optional, Type, Union, AsyncGenerator, Tuple, Callable, Literal
from services.llm import make_llm_api_call
from agentpress.tool import Tool, ToolResult
from agentpress.tool_registry import ToolRegistry
from agentpress.response_processor import (
    ResponseProcessor, 
    ProcessorConfig    
)
from services.supabase import DBConnection
from utils.logger import logger

# Type alias for tool choice
ToolChoice = Literal["auto", "required", "none"]

class ThreadManager:
    """Manages conversation threads with LLM models and tool execution.
    
    Provides comprehensive conversation management, handling message threading,
    tool registration, and LLM interactions with support for both standard and
    XML-based tool execution patterns.
    """

    def __init__(self):
        """Initialize ThreadManager."""
        self.db = DBConnection()
        self.tool_registry = ToolRegistry()
        self.response_processor = ResponseProcessor(
            tool_registry=self.tool_registry,
            add_message_callback=self.add_message
        )

    def add_tool(self, tool_class: Type[Tool], function_names: Optional[List[str]] = None, **kwargs):
        """Add a tool to the ThreadManager."""
        self.tool_registry.register_tool(tool_class, function_names, **kwargs)

    async def add_message(
        self, 
        thread_id: str, 
        type: str, 
        content: Union[Dict[str, Any], List[Any], str], 
        is_llm_message: bool = False,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """Add a message to the thread in the database.

        Args:
            thread_id: The ID of the thread to add the message to.
            type: The type of the message (e.g., 'text', 'image_url', 'tool_call', 'tool', 'user', 'assistant').
            content: The content of the message. Can be a dictionary, list, or string.
                     It will be stored as JSONB in the database.
            is_llm_message: Flag indicating if the message originated from the LLM.
                            Defaults to False (user message).
            metadata: Optional dictionary for additional message metadata.
                      Defaults to None, stored as an empty JSONB object if None.
        """
        logger.debug(f"Adding message of type '{type}' to thread {thread_id}")
        client = await self.db.client
        
        # Prepare data for insertion
        data_to_insert = {
            'thread_id': thread_id,
            'type': type,
            'content': json.dumps(content) if isinstance(content, (dict, list)) else content,
            'is_llm_message': is_llm_message,
            'metadata': json.dumps(metadata or {}), # Ensure metadata is always a JSON object
        }
        
        try:
            result = await client.table('messages').insert(data_to_insert).execute()
            logger.info(f"Successfully added message to thread {thread_id}")
        except Exception as e:
            logger.error(f"Failed to add message to thread {thread_id}: {str(e)}", exc_info=True)
            raise

    async def get_messages(self, thread_id: str) -> List[Dict[str, Any]]:
        """Get all messages for a thread.
        
        Args:
            thread_id: The ID of the thread to get messages for.
            
        Returns:
            List of message objects.
        """
        logger.debug(f"Getting messages for thread {thread_id}")
        client = await self.db.client
        
        try:
            result = await client.rpc('get_llm_formatted_messages', {'p_thread_id': thread_id}).execute()
            
            # Parse the returned data which might be stringified JSON
            if not result.data:
                return []
                
            # Return properly parsed JSON objects
            messages = []
            for item in result.data:
                if isinstance(item, str):
                    try:
                        parsed_item = json.loads(item)
                        messages.append(parsed_item)
                    except json.JSONDecodeError:
                        logger.error(f"Failed to parse message: {item}")
                else:
                    messages.append(item)

            # Ensure tool_calls have properly formatted function arguments
            for message in messages:
                if message.get('tool_calls'):
                    for tool_call in message['tool_calls']:
                        if isinstance(tool_call, dict) and 'function' in tool_call:
                            # Ensure function.arguments is a string
                            if 'arguments' in tool_call['function'] and not isinstance(tool_call['function']['arguments'], str):
                                # Log and fix the issue
                                logger.warning(f"Found non-string arguments in tool_call, converting to string")
                                tool_call['function']['arguments'] = json.dumps(tool_call['function']['arguments'])

            return messages
            
        except Exception as e:
            logger.error(f"Failed to get messages for thread {thread_id}: {str(e)}", exc_info=True)
            return []

    async def run_thread(
        self,
        thread_id: str,
        system_prompt: Dict[str, Any],
        stream: bool = True,
        temporary_message: Optional[Dict[str, Any]] = None,
        llm_model: str = "gpt-4o",
        llm_temperature: float = 0,
        llm_max_tokens: Optional[int] = None,
        processor_config: Optional[ProcessorConfig] = None,
        tool_choice: ToolChoice = "auto",
        native_max_auto_continues: int = 25,
    ) -> Union[Dict[str, Any], AsyncGenerator]:
        """Run a conversation thread with LLM integration and tool execution.
        
        Args:
            thread_id: The ID of the thread to run
            system_prompt: System message to set the assistant's behavior
            stream: Use streaming API for the LLM response
            temporary_message: Optional temporary user message for this run only
            llm_model: The name of the LLM model to use
            llm_temperature: Temperature parameter for response randomness (0-1)
            llm_max_tokens: Maximum tokens in the LLM response
            processor_config: Configuration for the response processor
            tool_choice: Tool choice preference ("auto", "required", "none")
            native_max_auto_continues: Maximum number of automatic continuations when 
                                      finish_reason="tool_calls" (0 disables auto-continue)
            
        Returns:
            An async generator yielding response chunks or error dict
        """
        
        logger.info(f"Starting thread execution for thread {thread_id}")
        logger.debug(f"Parameters: model={llm_model}, temperature={llm_temperature}, max_tokens={llm_max_tokens}")
        logger.debug(f"Auto-continue: max={native_max_auto_continues}")
        
        # Control whether we need to auto-continue due to tool_calls finish reason
        auto_continue = True
        auto_continue_count = 0
        
        # Define inner function to handle a single run
        async def _run_once(temp_msg=None):
            try:
                # Ensure processor_config is available in this scope
                nonlocal processor_config
                
                # Use a default config if none was provided
                if processor_config is None:
                    processor_config = ProcessorConfig()
                
                # 1. Get messages from thread for LLM call
                messages = await self.get_messages(thread_id)
                
                # 2. Prepare messages for LLM call + add temporary message if it exists
                prepared_messages = [system_prompt]
                
                # Find the last user message index
                last_user_index = -1
                for i, msg in enumerate(messages):
                    if msg.get('role') == 'user':
                        last_user_index = i
                
                # Insert temporary message before the last user message if it exists
                if temp_msg and last_user_index >= 0:
                    prepared_messages.extend(messages[:last_user_index])
                    prepared_messages.append(temp_msg)
                    prepared_messages.extend(messages[last_user_index:])
                    logger.debug("Added temporary message before the last user message")
                else:
                    # If no user message or no temporary message, just add all messages
                    prepared_messages.extend(messages)
                    if temp_msg:
                        prepared_messages.append(temp_msg)
                        logger.debug("Added temporary message to the end of prepared messages")

                # 3. Create or use processor config - this is now redundant since we handle it above
                # but kept for consistency and clarity
                logger.debug(f"Processor config: XML={processor_config.xml_tool_calling}, Native={processor_config.native_tool_calling}, " 
                       f"Execute tools={processor_config.execute_tools}, Strategy={processor_config.tool_execution_strategy}")

                # 4. Prepare tools for LLM call
                openapi_tool_schemas = None
                if processor_config.native_tool_calling:
                    openapi_tool_schemas = self.tool_registry.get_openapi_schemas()
                    logger.debug(f"Retrieved {len(openapi_tool_schemas) if openapi_tool_schemas else 0} OpenAPI tool schemas")

                # 5. Make LLM API call
                logger.info("Making LLM API call")
                try:
                    llm_response = await make_llm_api_call(
                        prepared_messages,
                        llm_model,
                        temperature=llm_temperature,
                        max_tokens=llm_max_tokens,
                        tools=openapi_tool_schemas,
                        tool_choice=tool_choice if processor_config.native_tool_calling else None,
                        stream=stream
                    )
                    logger.debug("Successfully received raw LLM API response stream/object")

                except Exception as e:
                    logger.error(f"Failed to make LLM API call: {str(e)}", exc_info=True)
                    raise

                # 6. Process LLM response using the ResponseProcessor
                if stream:
                    logger.info("Processing streaming response")
                    response_generator = self.response_processor.process_streaming_response(
                        llm_response=llm_response,
                        thread_id=thread_id,
                        config=processor_config
                    )
                    
                    return response_generator
                else:
                    logger.info("Processing non-streaming response")
                    try:
                        response = await self.response_processor.process_non_streaming_response(
                            llm_response=llm_response,
                            thread_id=thread_id,
                            config=processor_config
                        )
                        return response
                    except Exception as e:
                        logger.error(f"Error in non-streaming response: {str(e)}", exc_info=True)
                        raise
              
            except Exception as e:
                logger.error(f"Error in run_thread: {str(e)}", exc_info=True)
                return {
                    "status": "error",
                    "message": str(e)
                }
        
        # Define a wrapper generator that handles auto-continue logic
        async def auto_continue_wrapper():
            nonlocal auto_continue, auto_continue_count
            
            while auto_continue and (native_max_auto_continues == 0 or auto_continue_count < native_max_auto_continues):
                # Reset auto_continue for this iteration
                auto_continue = False
                
                # Run the thread once
                response_gen = await _run_once(temporary_message if auto_continue_count == 0 else None)
                
                # Handle error responses
                if isinstance(response_gen, dict) and "status" in response_gen and response_gen["status"] == "error":
                    yield response_gen
                    return
                
                # Process each chunk
                async for chunk in response_gen:
                    # Check if this is a finish reason chunk with tool_calls
                    if chunk.get('type') == 'finish' and chunk.get('finish_reason') == 'tool_calls':
                        # Only auto-continue if enabled (max > 0)
                        if native_max_auto_continues > 0:
                            logger.info(f"Detected finish_reason='tool_calls', auto-continuing ({auto_continue_count + 1}/{native_max_auto_continues})")
                            auto_continue = True
                            auto_continue_count += 1
                            # Don't yield the finish chunk to avoid confusing the client
                            continue
                    
                    # Otherwise just yield the chunk normally
                    yield chunk
                
                # If not auto-continuing, we're done
                if not auto_continue:
                    break
                
            # If we've reached the max auto-continues, log a warning
            if auto_continue and auto_continue_count >= native_max_auto_continues:
                logger.warning(f"Reached maximum auto-continue limit ({native_max_auto_continues}), stopping.")
                yield {
                    "type": "content", 
                    "content": f"\n[Agent reached maximum auto-continue limit of {native_max_auto_continues}]"
                }
        
        # If auto-continue is disabled (max=0), just run once
        if native_max_auto_continues == 0:
            logger.info("Auto-continue is disabled (native_max_auto_continues=0)")
            return await _run_once(temporary_message)
        
        # Otherwise return the auto-continue wrapper generator
        return auto_continue_wrapper()
