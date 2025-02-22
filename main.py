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

load_dotenv()

# Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
PORT = int(os.getenv('PORT', 5050))
SYSTEM_MESSAGE = (
    "You are a helpful and bubbly AI assistant who loves to chat about "
    "anything the user is interested in and is prepared to offer them facts. "
    "You have a penchant for dad jokes, owl jokes, and rickrolling – subtly. "
    "Always stay positive, but work in a joke when appropriate."
)
VOICE = 'alloy'
LOG_EVENT_TYPES = [
    'error', 'response.content.done', 'rate_limits.updated',
    'response.done', 'input_audio_buffer.committed',
    'input_audio_buffer.speech_stopped', 'input_audio_buffer.speech_started',
    'session.created'
]
SHOW_TIMING_MATH = False

app = FastAPI()

# Initialize Kayako client
kayako_client = KayakoAPIClient(
    base_url=os.getenv('KAYAKO_API_URL'),
    email=os.getenv('KAYAKO_EMAIL'),
    password=os.getenv('KAYAKO_PASSWORD')
)

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
    response.say("Please wait while we connect your call to the A. I. voice assistant, powered by Twilio and the Open-A.I. Realtime API")
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
            print("Initializing new conversation state")
            
        def add_user_message(self, text):
            if text.strip():
                self.transcript.append({"role": "user", "content": text, "timestamp": datetime.now()})
                print(f"Added user message to transcript. Total messages: {len(self.transcript)}")
                
        def add_assistant_message(self, text):
            if text.strip():
                self.transcript.append({"role": "assistant", "content": text, "timestamp": datetime.now()})
                print(f"Added assistant message to transcript. Total messages: {len(self.transcript)}")
        
        def get_formatted_transcript(self):
            # Prepare ticket content with metadata
            lines = []
            
            # Add metadata header with HTML formatting
            call_duration = (datetime.now() - self.call_start_time).total_seconds()
            lines.extend([
                "<h2>Call Information</h2>",
                "<hr/>",
                f"<p><strong>Duration:</strong> {call_duration:.2f} seconds</p>",
                f"<p><strong>Start Time:</strong> {self.call_start_time}</p>",
                f"<p><strong>End Time:</strong> {datetime.now()}</p>",
                f"<p><strong>Total Messages:</strong> {len(self.transcript)}</p>",
                "",
                "<h2>Conversation Transcript</h2>",
                "<hr/>",
                "<div class='transcript'>"
            ])
            
            # Add conversation messages with HTML formatting
            for msg in self.transcript:
                timestamp = msg["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
                role_style = "color: #2962FF" if msg["role"] == "assistant" else "color: #424242"
                lines.append(
                    f"<p style='{role_style}'>"
                    f"<strong>[{timestamp}] {msg['role'].title()}:</strong><br/>"
                    f"{msg['content']}"
                    f"</p>"
                )
            
            lines.append("</div>")  # Close transcript div
            
            print(f"Generating transcript with {len(self.transcript)} messages")
            return "\n".join(lines)

        def debug_print_transcript(self):
            """For console output, use plain text formatting"""
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
                        # Print final transcript state before creating ticket
                        print("\n=== Final Conversation State ===")
                        conversation.debug_print_transcript()
                        print("================================\n")
                        
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
                            f"Total Messages: {len(conversation.transcript)}\n\n"
                            f"Conversation Transcript\n"
                            f"====================\n\n"
                            f"{transcript_text}\n"  # Add newline after transcript
                        )
                        
                        ticket = Ticket(
                            subject=f'AI Call Assistant Conversation - {conversation.call_start_time.strftime("%Y-%m-%d %H:%M:%S")}',
                            contents=ticket_content,
                            channel='MAIL',
                            channel_id=1,
                            priority_id=3,
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
        }
    }
    print('Sending session update:', json.dumps(session_update))
    await openai_ws.send(json.dumps(session_update))

    # Uncomment the next line to have the AI speak first
    # await send_initial_conversation_item(openai_ws)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
