"""Transcription functionality using OpenAI's Whisper API."""

import json
from datetime import datetime
from pathlib import Path
from openai import AsyncOpenAI
import httpx
from typing import Dict, List, Optional

class WhisperTranscriber:
    def __init__(self):
        # Initialize OpenAI client with specific httpx client configuration
        timeout = httpx.Timeout(30.0)
        limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
        client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            limits=limits
        )
        self.client = AsyncOpenAI(http_client=client)
        self.model = "whisper-1"

    async def transcribe_file(self, audio_path: str) -> Optional[str]:
        """Transcribe a complete audio file using Whisper API."""
        try:
            with open(audio_path, "rb") as audio_file:
                response = await self.client.audio.transcriptions.create(
                    model=self.model,
                    file=audio_file,
                    language="en"
                )
                return response.text
                
        except Exception as e:
            print(f"Error transcribing audio file: {e}")
            return None

    async def transcribe_call(self, recording_data: Dict) -> List[Dict]:
        """Transcribe the complete conversation with timing information."""
        try:
            # Get the full transcription
            if "audio_file" not in recording_data["recordings"]:
                print("No audio file found in recording data")
                return []
                
            transcription = await self.transcribe_file(recording_data["recordings"]["audio_file"])
            if not transcription:
                return []
            
            # Create transcription entries with timing from utterances
            transcribed_utterances = []
            for utterance in recording_data["recordings"]["utterances"]:
                transcribed_utterances.append({
                    "role": utterance["role"],
                    "start_time": datetime.fromisoformat(utterance["start_time"]),
                    "end_time": datetime.fromisoformat(utterance["end_time"]),
                    "text": transcription  # Full transcription for now - in future we could split by timestamp
                })
            
            return transcribed_utterances
            
        except Exception as e:
            print(f"Error transcribing call: {e}")
            return [] 