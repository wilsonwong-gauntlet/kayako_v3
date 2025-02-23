"""Audio recording functionality for Twilio Media Stream."""

import os
import base64
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import wave

class AudioSegment:
    def __init__(self, role: str, timestamp: datetime, audio_data: bytes):
        self.role = role
        self.timestamp = timestamp
        self.audio_data = audio_data

class AudioRecorder:
    def __init__(self, stream_sid: str):
        self.stream_sid = stream_sid
        self.recordings_dir = Path("call_recordings")
        self.recordings_dir.mkdir(exist_ok=True)
        
        # Create directory for this specific call
        self.call_dir = self.recordings_dir / stream_sid
        self.call_dir.mkdir(exist_ok=True)
        
        # Store audio segments chronologically
        self.segments: List[AudioSegment] = []
        self.recording_start_time = datetime.now()

    def add_audio_chunk(self, audio_payload: str, is_assistant: bool = False):
        """Add an audio chunk with timestamp."""
        try:
            # Decode base64 audio data
            audio_data = base64.b64decode(audio_payload)
            
            # Create new segment with timestamp
            segment = AudioSegment(
                role="assistant" if is_assistant else "user",
                timestamp=datetime.now(),
                audio_data=audio_data
            )
            
            # Add to chronological list
            self.segments.append(segment)
                
        except Exception as e:
            print(f"Error writing audio chunk: {e}")

    def close(self) -> Optional[Dict]:
        """Close the recording session and save chronological audio file."""
        try:
            if not self.segments:
                print("No audio segments recorded")
                return None
                
            # Sort segments by timestamp
            self.segments.sort(key=lambda x: x.timestamp)
            
            # Create single WAV file for the entire conversation
            wav_path = self.call_dir / "conversation.wav"
            with wave.open(str(wav_path), 'wb') as wav_file:
                wav_file.setnchannels(1)  # mono
                wav_file.setsampwidth(1)  # 1 byte per sample for mulaw
                wav_file.setframerate(8000)  # 8kHz sample rate
                
                # Write all segments in chronological order
                utterances = []
                current_role = None
                current_start = None
                
                for segment in self.segments:
                    # If role changed, save previous utterance info
                    if current_role and current_role != segment.role:
                        utterances.append({
                            "role": current_role,
                            "start_time": current_start.isoformat(),
                            "end_time": segment.timestamp.isoformat()
                        })
                        current_start = segment.timestamp
                    
                    # Initialize new utterance if needed
                    if not current_role or current_role != segment.role:
                        current_role = segment.role
                        current_start = segment.timestamp
                    
                    # Write audio data
                    wav_file.writeframes(segment.audio_data)
                
                # Add final utterance
                if current_role:
                    utterances.append({
                        "role": current_role,
                        "start_time": current_start.isoformat(),
                        "end_time": datetime.now().isoformat()
                    })
            
            # Save metadata with chronological information
            metadata = {
                "audio_file": str(wav_path),
                "start_time": self.recording_start_time.isoformat(),
                "end_time": datetime.now().isoformat(),
                "utterances": utterances  # Chronological list of utterances with timing
            }
            
            # Save metadata
            metadata_file = self.call_dir / "recording_info.json"
            with open(metadata_file, "w") as f:
                json.dump(metadata, f, indent=2)
            
            print(f"Saved conversation with {len(utterances)} utterances in chronological order")
            return {
                "metadata_file": str(metadata_file),
                "recordings": metadata
            }
            
        except Exception as e:
            print(f"Error closing recording session: {e}")
            return None 