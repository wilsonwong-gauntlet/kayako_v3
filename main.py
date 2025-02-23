"""Main FastAPI application for Twilio Media Stream handling."""

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
from datetime import datetime

from src.api.kayako.client import KayakoAPIClient
from src.api.kayako.interfaces import Ticket
from src.kb.search import KBSearchEngine
from src.conversation.state import ConversationState
from src.openai.session import initialize_session, send_initial_conversation_item, VOICE
from src.openai.handler import OpenAIHandler
from src.config.system_message import SYSTEM_MESSAGE
from src.config.tools import TOOLS
from src.audio import AudioRecorder, WhisperTranscriber

load_dotenv()

# Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
PORT = int(os.getenv('PORT', 5050))

# Constants
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

    # Initialize conversation state and audio recorder
    conversation = ConversationState()
    audio_recorder = None
    
    async with websockets.connect(
        'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01',
        extra_headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "OpenAI-Beta": "realtime=v1"
        }
    ) as openai_ws:
        await initialize_session(openai_ws)
        
        # Initialize OpenAI handler
        openai_handler = OpenAIHandler(openai_ws, websocket, conversation, kb_search_engine)
        
        async def receive_from_twilio():
            """Receive audio data from Twilio and send it to the OpenAI Realtime API."""
            nonlocal audio_recorder
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    if data['event'] == 'media' and openai_ws.open:
                        openai_handler.latest_media_timestamp = int(data['media']['timestamp'])
                        audio_append = {
                            "type": "input_audio_buffer.append",
                            "audio": data['media']['payload']
                        }
                        # Record user audio
                        if audio_recorder:
                            audio_recorder.add_audio_chunk(data['media']['payload'])
                        await openai_ws.send(json.dumps(audio_append))
                    elif data['event'] == 'start':
                        openai_handler.stream_sid = data['start']['streamSid']
                        print(f"Incoming stream has started {openai_handler.stream_sid}")
                        # Initialize audio recorder
                        audio_recorder = AudioRecorder(openai_handler.stream_sid)
                        openai_handler.response_start_timestamp_twilio = None
                        openai_handler.latest_media_timestamp = 0
                        openai_handler.last_assistant_item = None
                    elif data['event'] == 'mark':
                        if openai_handler.mark_queue:
                            openai_handler.mark_queue.pop(0)
                    elif data['event'] == 'stop':
                        print(f"Call ended, creating ticket for stream {openai_handler.stream_sid}")
                        try:
                            # Close audio recorder and create Kayako ticket
                            recording_data = audio_recorder.close() if audio_recorder else None
                            if recording_data:
                                print(f"Audio recording data: {json.dumps(recording_data, indent=2)}")
                            await create_kayako_ticket(conversation, openai_handler.stream_sid, recording_data)
                        except Exception as e:
                            print(f"Error during ticket creation: {str(e)}")
                            import traceback
                            print(f"Full traceback: {traceback.format_exc()}")
            except WebSocketDisconnect:
                print("Client disconnected.")
                if openai_ws.open:
                    await openai_ws.close()
            except Exception as e:
                print(f"Error in receive_from_twilio: {str(e)}")
                import traceback
                print(f"Full traceback: {traceback.format_exc()}")

        async def send_to_twilio():
            """Receive events from the OpenAI Realtime API, send audio back to Twilio."""
            try:
                async for openai_message in openai_ws:
                    response = json.loads(openai_message)
                    
                    # Log events except audio delta
                    if response.get('type') != 'response.audio.delta':
                        print(f"\n=== OpenAI Event ===")
                        print(f"Event Type: {response.get('type')}")
                        print(f"Full Event Data: {json.dumps(response, indent=2)}")
                        print("===================\n")

                    # Handle user speech-to-text
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
                    
                    # Handle function calls and finalize assistant response
                    elif response.get('type') == 'response.done':
                        if 'response' in response and 'output' in response['response']:
                            # Handle function calls
                            for output_item in response['response']['output']:
                                if output_item.get('type') == 'function_call':
                                    await openai_handler.handle_function_call(output_item)
                            
                            # Handle assistant messages
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
                        
                        conversation.current_assistant_response = []

                    # Handle audio responses
                    if response.get('type') == 'response.audio.delta' and 'delta' in response:
                        audio_payload = base64.b64encode(base64.b64decode(response['delta'])).decode('utf-8')
                        # Record assistant audio
                        if audio_recorder:
                            audio_recorder.add_audio_chunk(audio_payload, is_assistant=True)
                        audio_delta = {
                            "event": "media",
                            "streamSid": openai_handler.stream_sid,
                            "media": {
                                "payload": audio_payload
                            }
                        }
                        await websocket.send_json(audio_delta)

                        if openai_handler.response_start_timestamp_twilio is None:
                            openai_handler.response_start_timestamp_twilio = openai_handler.latest_media_timestamp

                        if response.get('item_id'):
                            openai_handler.last_assistant_item = response['item_id']

                        await openai_handler.send_mark()

                    # Handle speech interruption
                    if response.get('type') == 'input_audio_buffer.speech_started':
                        print("Speech started detected.")
                        if openai_handler.last_assistant_item:
                            print(f"Interrupting response with id: {openai_handler.last_assistant_item}")
                            await openai_handler.handle_speech_started()

            except Exception as e:
                print(f"Error in send_to_twilio: {e}")

        await asyncio.gather(receive_from_twilio(), send_to_twilio())

async def create_kayako_ticket(conversation: ConversationState, stream_sid: str, recording_data: dict = None) -> None:
    """Create a Kayako ticket from the conversation."""
    try:
        print(f"Starting ticket creation for stream {stream_sid}")
        
        # Verify Kayako credentials
        if not all([os.getenv('KAYAKO_API_URL'), os.getenv('KAYAKO_EMAIL'), os.getenv('KAYAKO_PASSWORD')]):
            raise ValueError("Missing required Kayako credentials in environment variables")
        
        # Get conversation summary
        analysis = conversation.get_conversation_summary()
        print(f"Conversation summary: {analysis}")
        
        # Calculate call duration
        call_duration = (datetime.now() - conversation.call_start_time).total_seconds()
        
        # Create ticket content
        ticket_content = f"Call Duration: {int(call_duration)} seconds\n\n"
        ticket_content += "=== Real-time Transcript ===\n"
        ticket_content += conversation.get_formatted_transcript()
        
        # Add Whisper transcription if available
        if recording_data and "recordings" in recording_data:
            try:
                print("Starting Whisper transcription")
                transcriber = WhisperTranscriber()
                transcription = await transcriber.transcribe_file(recording_data["recordings"]["audio_file"])
                    
            except Exception as e:
                print(f"Error during Whisper transcription: {e}")
                print(f"Full error details: {str(e)}")
                import traceback
                print(f"Transcription error traceback: {traceback.format_exc()}")
                ticket_content += f"\n\nNote: Whisper transcription failed: {str(e)}"
        
        # Create and submit ticket
        print("Creating Kayako ticket")
        print(f"Ticket content length: {len(ticket_content)} characters")
        
        ticket = Ticket(
            subject=f'AI Call Assistant Conversation - {conversation.call_start_time.strftime("%Y-%m-%d %H:%M:%S")}',
            contents=ticket_content,
            channel='MAIL',
            channel_id=1,
            requester_id=309,  # Using a default requester ID
            priority_id=2,  # Normal priority
            type_id=1  # Question type
        )
        
        ticket_id = await kayako_client.create_ticket(ticket)
        print(f"Successfully created Kayako ticket with ID: {ticket_id}")
        
    except Exception as e:
        print(f"Error creating Kayako ticket: {e}")
        print(f"Error details: {str(e)}")
        import traceback
        print(f"Full error traceback: {traceback.format_exc()}")
        print("Transcript that failed to save:")
        print(ticket_content)
        raise  # Re-raise the exception to ensure it's properly handled

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
