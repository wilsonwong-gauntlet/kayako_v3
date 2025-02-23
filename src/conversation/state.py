"""Conversation state management."""

from datetime import datetime
from typing import List, Dict, Optional

class ConversationState:
    def __init__(self):
        self.transcript: List[Dict] = []
        self.current_assistant_response: List[str] = []
        self.call_start_time: datetime = datetime.now()
        self.current_user_message: List[str] = []
        self.user_email: Optional[str] = None
        self.reason_for_calling: Optional[str] = None
        print("Initializing new conversation state")
        
    def add_user_message(self, text: str) -> None:
        """Add a user message to the transcript."""
        if text.strip():
            self.transcript.append({
                "role": "user",
                "content": text,
                "timestamp": datetime.now()
            })
            print(f"Added user message to transcript. Total messages: {len(self.transcript)}")
            
    def add_assistant_message(self, text: str) -> None:
        """Add an assistant message to the transcript."""
        if text.strip():
            self.transcript.append({
                "role": "assistant",
                "content": text,
                "timestamp": datetime.now()
            })
            print(f"Added assistant message to transcript. Total messages: {len(self.transcript)}")

    def get_conversation_summary(self) -> Dict[str, Optional[str]]:
        """Get the current state of the conversation."""
        return {
            "email": self.user_email,
            "reason": self.reason_for_calling or "Not clearly stated"
        }

    def get_formatted_transcript(self) -> str:
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

    def debug_print_transcript(self) -> None:
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