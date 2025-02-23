"""OpenAI message handling and function calls."""

import json
import base64
from typing import Dict, Any, Optional, List
import websockets
from fastapi import WebSocket
from src.conversation.state import ConversationState
from src.kb.search import KBSearchEngine

class OpenAIHandler:
    def __init__(self, 
                 openai_ws: websockets.WebSocketClientProtocol,
                 websocket: WebSocket,
                 conversation: ConversationState,
                 kb_search_engine: KBSearchEngine):
        self.openai_ws = openai_ws
        self.websocket = websocket
        self.conversation = conversation
        self.kb_search_engine = kb_search_engine
        self.stream_sid: Optional[str] = None
        self.latest_media_timestamp: int = 0
        self.last_assistant_item: Optional[str] = None
        self.mark_queue: List[str] = []
        self.response_start_timestamp_twilio: Optional[int] = None

    async def handle_function_call(self, output_item: Dict[str, Any]) -> None:
        """Handle function calls from the OpenAI API."""
        try:
            arguments = json.loads(output_item['arguments'])
            
            if output_item['name'] == 'search_knowledge_base':
                await self._handle_kb_search(output_item['call_id'], arguments)
            elif output_item['name'] == 'save_user_email':
                await self._handle_save_email(output_item['call_id'], arguments)
            elif output_item['name'] == 'set_reason_for_calling':
                await self._handle_set_reason(output_item['call_id'], arguments)
            
            # Generate a new response after handling any function
            await self.openai_ws.send(json.dumps({"type": "response.create"}))
            
        except Exception as e:
            print(f"Error handling function call: {e}")
            await self._send_error_output(output_item['call_id'], str(e))

    async def _handle_kb_search(self, call_id: str, arguments: Dict[str, Any]) -> None:
        """Handle knowledge base search function."""
        if not self.kb_search_engine.initialized:
            print("Initializing KB search engine...")
            await self.kb_search_engine.initialize()
        
        print("Searching knowledge base...")
        summary = await self.kb_search_engine.search_and_summarize(arguments["query"])
        print(f"Search result: {summary}")
        
        await self._send_function_output(call_id, {
            "result": summary if summary else "No relevant information found in the AdvocateHub knowledge base."
        })

    async def _handle_save_email(self, call_id: str, arguments: Dict[str, Any]) -> None:
        """Handle saving user email function."""
        email = arguments.get("email")
        if email:
            self.conversation.user_email = email
            print(f"Saved user email: {email}")
        
        await self._send_function_output(call_id, {
            "result": "Email saved successfully."
        })

    async def _handle_set_reason(self, call_id: str, arguments: Dict[str, Any]) -> None:
        """Handle setting reason for calling function."""
        reason = arguments.get("reason")
        if reason:
            self.conversation.reason_for_calling = reason
            print(f"Saved reason for calling: {reason}")
        
        await self._send_function_output(call_id, {
            "result": "Reason for calling saved successfully."
        })

    async def _send_function_output(self, call_id: str, result: Dict[str, Any]) -> None:
        """Send function output back to OpenAI."""
        function_output = {
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps(result)
            }
        }
        await self.openai_ws.send(json.dumps(function_output))

    async def _send_error_output(self, call_id: str, error: str) -> None:
        """Send error output back to OpenAI."""
        error_output = {
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps({"error": error})
            }
        }
        await self.openai_ws.send(json.dumps(error_output))
        await self.openai_ws.send(json.dumps({"type": "response.create"}))

    async def handle_speech_started(self) -> None:
        """Handle interruption when the caller's speech starts."""
        print("Handling speech started event.")
        if self.mark_queue and self.response_start_timestamp_twilio is not None:
            elapsed_time = self.latest_media_timestamp - self.response_start_timestamp_twilio

            if self.last_assistant_item:
                truncate_event = {
                    "type": "conversation.item.truncate",
                    "item_id": self.last_assistant_item,
                    "content_index": 0,
                    "audio_end_ms": elapsed_time
                }
                await self.openai_ws.send(json.dumps(truncate_event))

            await self.websocket.send_json({
                "event": "clear",
                "streamSid": self.stream_sid
            })

            self.mark_queue.clear()
            self.last_assistant_item = None
            self.response_start_timestamp_twilio = None

    async def send_mark(self) -> None:
        """Send mark event to Twilio."""
        if self.stream_sid:
            mark_event = {
                "event": "mark",
                "streamSid": self.stream_sid,
                "mark": {"name": "responsePart"}
            }
            await self.websocket.send_json(mark_event)
            self.mark_queue.append('responsePart') 