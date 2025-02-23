import os
import json
import base64
import asyncio
import websockets
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream
from dotenv import load_dotenv
from src.api.kayako.client import KayakoAPIClient
from src.api.kayako.interfaces import Ticket
from datetime import datetime
from src.kb.search import KBSearchEngine
import re

load_dotenv()

# Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
PORT = int(os.getenv('PORT', 5050))
SYSTEM_MESSAGE = (
    "You are an AI voice assistant for Kayako's AdvocateHub support system. Your primary role is to help customers "
    "by providing accurate information from our knowledge base and ensuring proper ticket creation for follow-up. "
    "\n\n"
    "CORE RESPONSIBILITIES:\n"
    "1. Always search the knowledge base using search_knowledge_base function for ANY questions about AdvocateHub.\n"
    "2. If a clear answer is found in the knowledge base, provide it to the user.\n"
    "3. If no answer is found, inform the user that a support expert will follow up and end the conversation professionally.\n"
    "\n"
    "CONVERSATION FLOW:\n"
    "1. Start with a warm greeting and immediately ask for their email address for follow-up purposes.\n"
    "2. Once you have their email (or after 3 attempts), ask about their reason for calling.\n"
    "3. When they explain their issue, use set_reason_for_calling to save a clear summary.\n"
    "4. Search the knowledge base for relevant information.\n"
    "5. Either provide the answer or inform them that an expert will follow up.\n"
    "\n"
    "EMAIL COLLECTION:\n"
    "- Ask: 'Before we dive in, could you please share your email address for follow-up purposes?'\n"
    "- You can understand formats like 'user at gmail dot com'\n"
    "- If unclear, say: 'I apologize, but I didn't catch a valid email address. Could you please provide it in a format like username@domain.com?'\n"
    "- After 3 failed attempts, proceed with their request\n"
    "\n"
    "COMMUNICATION STYLE:\n"
    "- Maintain a professional yet friendly tone\n"
    "- Be clear and concise\n"
    "- Show empathy and understanding\n"
    "- Stay positive and solution-focused\n"
    "\n"
    "Remember: Your goal is to either resolve the issue immediately with knowledge base information or ensure "
    "a smooth handoff to the support team."
)
VOICE = 'alloy'
LOG_EVENT_TYPES = [
    'error', 'response.content.done', 'rate_limits.updated',
    'response.done', 'input_audio_buffer.committed',
    'input_audio_buffer.speech_stopped', 'input_audio_buffer.speech_started',
    'session.created'
]
SHOW_TIMING_MATH = False

# Define tools configuration
TOOLS = [
    {
        "type": "function",
        "name": "search_knowledge_base",
        "description": "Query a knowledge base to retrieve relevant info on a topic.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The user question or search query about AdvocateHub."
                }
            },
            "required": ["query"]
        }
    },
    {
        "type": "function",
        "name": "save_user_email",
        "description": "Save the user's email address when they provide it.",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "The email address provided by the user."
                }
            },
            "required": ["email"]
        }
    },
    {
        "type": "function",
        "name": "set_reason_for_calling",
        "description": "Set the user's reason for calling once they've clearly stated their issue.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "A clear, concise summary of why the user is calling."
                }
            },
            "required": ["reason"]
        }
    }
]


app = FastAPI()

# Initialize Kayako client
kayako_client = KayakoAPIClient(
    base_url=os.getenv('KAYAKO_API_URL'),
    email=os.getenv('KAYAKO_EMAIL'),
    password=os.getenv('KAYAKO_PASSWORD')
)

# Initialize KB search engine
kb_search_engine = KBSearchEngine()

if not OPENAI_API_KEY:
    raise ValueError('Missing the OpenAI API key. Please set it in the .env file.')

@app.get("/", response_class=JSONResponse)
async def index_page():
    return {"message": "Twilio Media Stream Server is running!"}

@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    """Handle incoming call and return TwiML response to connect to Media Stream."""
    response = VoiceResponse()
    # <Say> punctuation to improve text-to-speech flow
    response.say("Please wait while we connect your call to the AI voice assistant, powered by Twilio and the Open-A.I. Realtime API")
    response.pause(length=1)
    response.say("O.K. you can start talking!")
    host = request.url.hostname
    connect = Connect()
    connect.stream(url=f'wss://{host}/media-stream')
    response.append(connect)
    return HTMLResponse(content=str(response), media_type="application/xml")

@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    """Handle WebSocket connections between Twilio and OpenAI."""
    print("Client connected")
    await websocket.accept()

    # Initialize conversation state
    class ConversationState:
        def __init__(self):
            self.transcript = []
            self.current_assistant_response = []
            self.call_start_time = datetime.now()
            self.current_user_message = []
            self.user_email = None
            self.reason_for_calling = None
            print("Initializing new conversation state")
            
        def add_user_message(self, text):
            if text.strip():
                self.transcript.append({"role": "user", "content": text, "timestamp": datetime.now()})
                print(f"Added user message to transcript. Total messages: {len(self.transcript)}")
                
        def add_assistant_message(self, text):
            if text.strip():
                self.transcript.append({"role": "assistant", "content": text, "timestamp": datetime.now()})
                print(f"Added assistant message to transcript. Total messages: {len(self.transcript)}")

        def get_conversation_summary(self) -> dict:
            """Get the current state of the conversation."""
            return {
                "email": self.user_email,
                "reason": self.reason_for_calling or "Not clearly stated"
            }

        def get_formatted_transcript(self):
            """Format transcript with HTML styling focused on key support information."""
            lines = []
            
            # Add customer information section
            lines.extend([
                "<h2>Customer Information</h2>",
                "<hr/>",
                f"<p><strong>Email:</strong> {self.user_email or 'Not provided'}</p>",
                "",
                "<h2>Support Request Details</h2>",
                "<hr/>",
            ])

            # Add reason for calling if available
            if self.reason_for_calling:
                lines.append(f"<p><strong>Reason for Call:</strong> {self.reason_for_calling}</p>")
            
            # Add conversation transcript with clear formatting
            lines.extend([
                "",
                "<h2>Conversation History</h2>",
                "<hr/>",
                "<div class='transcript' style='margin-left: 20px;'>"
            ])
            
            # Add conversation messages with improved styling
            for msg in self.transcript:
                timestamp = msg["timestamp"].strftime("%H:%M:%S")  # Simplified timestamp
                if msg["role"] == "assistant":
                    style = "color: #2962FF; margin-bottom: 15px;"
                    prefix = "AI Assistant"
                else:
                    style = "color: #424242; margin-bottom: 15px;"
                    prefix = "Customer"
                
                lines.append(
                    f"<p style='{style}'>"
                    f"<strong>[{timestamp}] {prefix}:</strong><br/>"
                    f"{msg['content']}"
                    f"</p>"
                )
            
            lines.append("</div>")  # Close transcript div
            
            return "\n".join(lines)

        def debug_print_transcript(self):
            """Print transcript to console for debugging."""
            print("\n=== Current Transcript State ===")
            print(f"Total messages: {len(self.transcript)}")
            
            # Print metadata
            call_duration = (datetime.now() - self.call_start_time).total_seconds()
            print("\nCall Information")
            print("================")
            print(f"Duration: {call_duration:.2f} seconds")
            print(f"Start Time: {self.call_start_time}")
            print(f"End Time: {datetime.now()}")
            print(f"Total Messages: {len(self.transcript)}")
            print(f"User Email: {self.user_email or 'Not provided'}")
            
            # Print messages
            print("\nConversation Transcript")
            print("=====================\n")
            for msg in self.transcript:
                timestamp = msg["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
                print(f"[{timestamp}] {msg['role'].title()}: {msg['content']}\n")
            
            print("===============================\n")

    conversation = ConversationState()
    
    async with websockets.connect(
        'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01',
        extra_headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "OpenAI-Beta": "realtime=v1"
        }
    ) as openai_ws:
        await initialize_session(openai_ws)

        # Connection specific state
        stream_sid = None
        latest_media_timestamp = 0
        last_assistant_item = None
        mark_queue = []
        response_start_timestamp_twilio = None
        
        async def receive_from_twilio():
            """Receive audio data from Twilio and send it to the OpenAI Realtime API."""
            nonlocal stream_sid, latest_media_timestamp
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    if data['event'] == 'media' and openai_ws.open:
                        latest_media_timestamp = int(data['media']['timestamp'])
                        audio_append = {
                            "type": "input_audio_buffer.append",
                            "audio": data['media']['payload']
                        }
                        await openai_ws.send(json.dumps(audio_append))
                    elif data['event'] == 'start':
                        stream_sid = data['start']['streamSid']
                        print(f"Incoming stream has started {stream_sid}")
                        response_start_timestamp_twilio = None
                        latest_media_timestamp = 0
                        last_assistant_item = None
                    elif data['event'] == 'mark':
                        if mark_queue:
                            mark_queue.pop(0)
                    elif data['event'] == 'stop':
                        # Analyze transcript for email and reason
                        analysis = conversation.get_conversation_summary()
                        
                        # Calculate call duration
                        call_duration = (datetime.now() - conversation.call_start_time).total_seconds()
                        
                        # Create Kayako ticket when call ends
                        transcript_text = conversation.get_formatted_transcript()
                        
                        # Prepare ticket content with metadata
                        ticket_content = (
                            f"Call Duration: {call_duration:.2f} seconds\n"
                            f"Call Start Time: {conversation.call_start_time}\n"
                            f"Call End Time: {datetime.now()}\n"
                            f"Stream SID: {stream_sid}\n"
                            f"Total Messages: {len(conversation.transcript)}\n"
                            f"User Email: {analysis['email'] or 'Not provided'}\n\n"
                            f"Reason for Call: {analysis['reason']}\n\n"
                            f"Conversation Transcript\n"
                            f"====================\n\n"
                            f"{transcript_text}\n"  # Add newline after transcript
                        )
                        
                        
                    
                        ticket = Ticket(
                            subject=f'AI Call Assistant Conversation - {conversation.call_start_time.strftime("%Y-%m-%d %H:%M:%S")}',
                            contents=ticket_content,
                            channel='MAIL',
                            channel_id=1,
                            requester_id=309  # Using a default requester ID
                        )
                        try:
                            ticket_id = await kayako_client.create_ticket(ticket)
                            print(f"Created Kayako ticket with ID: {ticket_id}")
                            print(f"Ticket content length: {len(ticket_content)} characters")
                        except Exception as e:
                            print(f"Error creating Kayako ticket: {e}")
                            print("Transcript that failed to save:")
                            print(transcript_text)
            except WebSocketDisconnect:
                print("Client disconnected.")
                if openai_ws.open:
                    await openai_ws.close()

        async def send_to_twilio():
            """Receive events from the OpenAI Realtime API, send audio back to Twilio."""
            nonlocal stream_sid, last_assistant_item, response_start_timestamp_twilio
            try:
                async for openai_message in openai_ws:
                    response = json.loads(openai_message)
                    
                    # Log ALL events except audio delta
                    if response.get('type') != 'response.audio.delta':
                        print(f"\n=== OpenAI Event ===")
                        print(f"Event Type: {response.get('type')}")
                        print(f"Full Event Data: {json.dumps(response, indent=2)}")
                        print("===================\n")

                    # Original event handling continues...
                    if response['type'] in LOG_EVENT_TYPES:
                        print(f"Received event: {response['type']}", response)

                    # Handle user speech-to-text with the new event type
                    if response.get('type') == 'conversation.item.input_audio_transcription.completed':
                        if 'transcript' in response:
                            user_text = response['transcript']
                            print(f"\n=== New User Message ===")
                            print(f"Received text: {user_text}")
                            conversation.add_user_message(user_text)
                            conversation.debug_print_transcript()

                    # Handle assistant responses
                    if response.get('type') == 'response.content.part':
                        if 'content' in response and 'text' in response['content']:
                            part_text = response['content']['text']
                            print(f"Received partial assistant response: {part_text}")
                            conversation.current_assistant_response.append(part_text)
                    
                    # Finalize assistant response when it's complete
                    elif response.get('type') == 'response.done':
                        # Get the complete response from the output transcript
                        if 'response' in response and 'output' in response['response']:
                            # First handle any function calls
                            for output_item in response['response']['output']:
                                if output_item.get('type') == 'function_call':
                                    print(f"Function call detected: {output_item.get('name')}")
                                    
                                    # Parse arguments first
                                    try:
                                        arguments = json.loads(output_item['arguments'])
                                    except Exception as e:
                                        print(f"Error parsing function arguments: {e}")
                                        continue
                                    
                                    try:
                                        # Handle search_knowledge_base function
                                        if output_item['name'] == 'search_knowledge_base':
                                            # Initialize KB search if not done yet
                                            if not kb_search_engine.initialized:
                                                print("Initializing KB search engine...")
                                                await kb_search_engine.initialize()
                                            
                                            print("Searching knowledge base...")
                                            summary = await kb_search_engine.search_and_summarize(arguments["query"])
                                            print(f"Search result: {summary}")
                                            
                                            function_output = {
                                                "type": "conversation.item.create",
                                                "item": {
                                                    "type": "function_call_output",
                                                    "call_id": output_item['call_id'],
                                                    "output": json.dumps({"result": summary if summary else "No relevant information found in the AdvocateHub knowledge base."})
                                                }
                                            }
                                            await openai_ws.send(json.dumps(function_output))
                                            
                                        elif output_item['name'] == 'save_user_email':
                                            email = arguments.get("email")
                                            if email:
                                                conversation.user_email = email
                                                print(f"Saved user email: {email}")
                                                
                                            function_output = {
                                                "type": "conversation.item.create",
                                                "item": {
                                                    "type": "function_call_output",
                                                    "call_id": output_item['call_id'],
                                                    "output": json.dumps({"result": "Email saved successfully."})
                                                }
                                            }
                                            await openai_ws.send(json.dumps(function_output))
                                            
                                        elif output_item['name'] == 'set_reason_for_calling':
                                            reason = arguments.get("reason")
                                            if reason:
                                                conversation.reason_for_calling = reason
                                                print(f"Saved reason for calling: {reason}")
                                                
                                            function_output = {
                                                "type": "conversation.item.create",
                                                "item": {
                                                    "type": "function_call_output",
                                                    "call_id": output_item['call_id'],
                                                    "output": json.dumps({"result": "Reason for calling saved successfully."})
                                                }
                                            }
                                            await openai_ws.send(json.dumps(function_output))
                                            
                                        # Generate a new response after handling any function
                                        await openai_ws.send(json.dumps({"type": "response.create"}))
                                        
                                    except Exception as e:
                                        print(f"Error handling function call: {e}")
                                        error_output = {
                                            "type": "conversation.item.create",
                                            "item": {
                                                "type": "function_call_output",
                                                "call_id": output_item['call_id'],
                                                "output": json.dumps({"error": str(e)})
                                            }
                                        }
                                        await openai_ws.send(json.dumps(error_output))
                                        await openai_ws.send(json.dumps({"type": "response.create"}))
                            
                            # Then handle any assistant messages
                            for output_item in response['response']['output']:
                                if output_item.get('role') == 'assistant' and output_item.get('content'):
                                    for content in output_item['content']:
                                        if content.get('type') == 'audio' and 'transcript' in content:
                                            full_response = content['transcript']
                                            print(f"\n=== New Assistant Message ===")
                                            print(f"Full response: {full_response}")
                                            conversation.add_assistant_message(full_response)
                                            conversation.debug_print_transcript()
                                            break
                        
                        # Clear the partial response buffer
                        conversation.current_assistant_response = []

                    if response.get('type') == 'response.audio.delta' and 'delta' in response:
                        audio_payload = base64.b64encode(base64.b64decode(response['delta'])).decode('utf-8')
                        audio_delta = {
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {
                                "payload": audio_payload
                            }
                        }
                        await websocket.send_json(audio_delta)

                        if response_start_timestamp_twilio is None:
                            response_start_timestamp_twilio = latest_media_timestamp
                            if SHOW_TIMING_MATH:
                                print(f"Setting start timestamp for new response: {response_start_timestamp_twilio}ms")

                        if response.get('item_id'):
                            last_assistant_item = response['item_id']

                        await send_mark(websocket, stream_sid)

                    if response.get('type') == 'input_audio_buffer.speech_started':
                        print("Speech started detected.")
                        if last_assistant_item:
                            print(f"Interrupting response with id: {last_assistant_item}")
                            await handle_speech_started_event()

            except Exception as e:
                print(f"Error in send_to_twilio: {e}")

        async def handle_speech_started_event():
            """Handle interruption when the caller's speech starts."""
            nonlocal response_start_timestamp_twilio, last_assistant_item
            print("Handling speech started event.")
            if mark_queue and response_start_timestamp_twilio is not None:
                elapsed_time = latest_media_timestamp - response_start_timestamp_twilio
                if SHOW_TIMING_MATH:
                    print(f"Calculating elapsed time for truncation: {latest_media_timestamp} - {response_start_timestamp_twilio} = {elapsed_time}ms")

                if last_assistant_item:
                    if SHOW_TIMING_MATH:
                        print(f"Truncating item with ID: {last_assistant_item}, Truncated at: {elapsed_time}ms")

                    truncate_event = {
                        "type": "conversation.item.truncate",
                        "item_id": last_assistant_item,
                        "content_index": 0,
                        "audio_end_ms": elapsed_time
                    }
                    await openai_ws.send(json.dumps(truncate_event))

                await websocket.send_json({
                    "event": "clear",
                    "streamSid": stream_sid
                })

                mark_queue.clear()
                last_assistant_item = None
                response_start_timestamp_twilio = None

        async def send_mark(connection, stream_sid):
            if stream_sid:
                mark_event = {
                    "event": "mark",
                    "streamSid": stream_sid,
                    "mark": {"name": "responsePart"}
                }
                await connection.send_json(mark_event)
                mark_queue.append('responsePart')

        await asyncio.gather(receive_from_twilio(), send_to_twilio())

async def send_initial_conversation_item(openai_ws):
    """Send initial conversation item if AI talks first."""
    initial_conversation_item = {
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": "Greet the user with 'Hello there! I am an AI voice assistant powered by Twilio and the OpenAI Realtime API. You can ask me for facts, jokes, or anything you can imagine. How can I help you?'"
                }
            ]
        }
    }
    await openai_ws.send(json.dumps(initial_conversation_item))
    await openai_ws.send(json.dumps({"type": "response.create"}))


async def initialize_session(openai_ws):
    """Control initial session with OpenAI."""
    session_update = {
        "type": "session.update",
        "session": {
            "turn_detection": {"type": "server_vad"},
            "input_audio_format": "g711_ulaw",
            "output_audio_format": "g711_ulaw",
            "input_audio_transcription": {
                "model": "whisper-1"
            },
            "voice": VOICE,
            "instructions": SYSTEM_MESSAGE,
            "modalities": ["text", "audio"],
            "temperature": 0.8,
            "tools": TOOLS,
            "tool_choice": "auto"
        }
    }
    print('Sending session update:', json.dumps(session_update))
    await openai_ws.send(json.dumps(session_update))

    # Uncomment the next line to have the AI speak first
    # await send_initial_conversation_item(openai_ws)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
