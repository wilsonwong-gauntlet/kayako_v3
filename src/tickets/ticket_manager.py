"""Ticket management functionality for handling customer support tickets."""

import re
from typing import List, Optional, Dict
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field
import os
import logging

from src.interfaces import Ticket
from src.kayako_client import KayakoAPIClient
from src.conversation.state import ConversationContext, Message

logger = logging.getLogger(__name__)

class TicketMetadata(BaseModel):
    """Additional metadata for ticket creation."""
    source: str = "voice_assistant"
    channel: str = "phone"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    conversation_id: str
    transcript: List[Dict[str, str]]

class TicketManager:
    """Manages support ticket creation and updates."""
    
    def __init__(self):
        """Initialize the ticket manager with real Kayako API."""
        self.api = KayakoAPIClient(
            base_url=os.getenv("KAYAKO_API_URL"),
            email=os.getenv("KAYAKO_EMAIL"),
            password=os.getenv("KAYAKO_PASSWORD")
        )
    
    def _validate_email(self, email: str) -> bool:
        """Validate email format."""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return bool(re.match(pattern, email))
    
    def _validate_phone(self, phone: str) -> bool:
        """Validate phone number format."""
        # Remove common separators and whitespace
        cleaned = re.sub(r'[\s\-\(\)\.]', '', phone)
        # Match various formats:
        # - 10 digits (US numbers)
        # - 11 digits starting with 1 (US numbers with country code)
        # - International numbers (up to 15 digits with optional + prefix)
        pattern = r'^\+?1?\d{10,14}$'
        return bool(re.match(pattern, cleaned))
    
    def _format_transcript(self, context: ConversationContext) -> List[Dict[str, str]]:
        """Format conversation transcript for ticket."""
        return [
            {
                "role": msg.role,
                "content": msg.content,
                "timestamp": msg.timestamp.isoformat()
            }
            for msg in context.messages
        ]
    
    def _determine_priority(self, context: ConversationContext) -> str:
        """Determine ticket priority based on conversation context."""
        # Check for keywords indicating urgency
        urgent_keywords = ["urgent", "emergency", "critical", "broken", "error"]
        
        # Check last few messages for urgent keywords
        recent_messages = " ".join(
            msg.content.lower() 
            for msg in context.messages[-3:] 
            if msg.role == "user"
        )
        
        if any(keyword in recent_messages for keyword in urgent_keywords):
            return "high"
        
        return "medium"
    
    async def create_ticket(
        self,
        context: ConversationContext,
        subject: str,
        contents: str,
        email: Optional[str] = None,
        phone: Optional[str] = None
    ) -> str:
        """
        Create a new support ticket.
        
        Args:
            context: Conversation context
            subject: Ticket subject
            contents: Ticket contents
            email: Customer email (optional)
            phone: Customer phone number (optional)
        
        Returns:
            Ticket ID
        
        Raises:
            ValueError: If neither email nor phone is provided
        """
        try:
            if not email and not phone:
                raise ValueError("Either email or phone number must be provided")
            
            if email and not self._validate_email(email):
                raise ValueError("Invalid email format")
            
            if phone and not self._validate_phone(phone):
                raise ValueError("Invalid phone number format")
            
            # Get or create user to get requester_id
            user = None
            if email:
                try:
                    logger.info(f"Looking up user by email: {email}")
                    user = await self.api.get_user_by_email(email)
                    if user:
                        logger.info(f"Found existing user with ID: {user.id}")
                    if not user:
                        logger.info(f"Creating new user for email: {email}")
                        from src.kb.interfaces import User
                        new_user = User(
                            id=0,  # Will be set by API
                            email=email,
                            full_name=email.split('@')[0],
                            phone=phone,
                            role=4,  # Customer role
                            locale=2  # en-US
                        )
                        user_id = await self.api.create_user(new_user)
                        logger.info(f"Created new user with ID: {user_id}")
                        user = await self.api.get_user_by_email(email)
                        if not user:
                            raise ValueError(f"Failed to retrieve newly created user for email: {email}")
                        logger.info(f"Retrieved fresh user object with ID: {user.id}")
                except Exception as e:
                    logger.error(f"Error in user creation/lookup: {str(e)}")
                    raise
            
            # Create ticket metadata
            metadata = TicketMetadata(
                conversation_id=context.conversation_id,
                transcript=self._format_transcript(context)
            )
            
            # Create ticket
            try:
                ticket = Ticket(
                    subject='Support Request from Voice Call',
                    contents=self.format_ticket_description(context),
                    channel="MAIL",
                    type_id=1,
                    priority_id=3,
                    requester_id=user.id if user else None
                )
                
                logger.info(f"Creating ticket with data: {ticket.dict()}")
                ticket_id = await self.api.create_ticket(ticket)
                logger.info(f"Successfully created ticket: {ticket_id}")
                return ticket_id
            except Exception as e:
                logger.error(f"Error creating ticket with data: {ticket.dict() if 'ticket' in locals() else 'No ticket data'}")
                logger.error(f"Full error: {str(e)}")
                raise
        
        except Exception as e:
            raise
    
    def extract_contact_info(self, message: str, context: Optional[ConversationContext] = None) -> Dict[str, Optional[str]]:
        """
        Extract email and phone number from message if present.
        
        Args:
            message: User message to extract from
            context: Optional conversation context to check previous messages
            
        Returns:
            Dictionary with 'email' and 'phone' keys
        """
        recent_messages = []
        if context:
            recent_messages = [
                msg.content for msg in context.messages[-5:]
                if msg.role == "user"
            ]
        # Add current message to the list
        recent_messages.append(message)
        
        combined_message = " ".join(recent_messages)
        logger.info(f"Processing message for contact info: {combined_message}")
        
        processed_message = re.sub(r'\b(?:at|@)\b', '@', combined_message, flags=re.IGNORECASE)
        processed_message = re.sub(r'\b(?:dot)\b', '.', processed_message, flags=re.IGNORECASE)
        logger.info(f"Processed message: {processed_message}")
        
        email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w{2,}', processed_message)
        email = email_match.group(0) if email_match else None
        
        if not email:
            username_match = re.search(r'([\w\.-]+)\s*(?:@|$)', processed_message)
            if username_match:
                username = username_match.group(1)
                if "gmail" in processed_message.lower():
                    email = f"{username}@gmail.com"
                elif "yahoo" in processed_message.lower():
                    email = f"{username}@yahoo.com"
                elif "hotmail" in processed_message.lower():
                    email = f"{username}@hotmail.com"
                elif "outlook" in processed_message.lower():
                    email = f"{username}@outlook.com"
        
        logger.info(f"Extracted email: {email}")
        
        phone_patterns = [
            r'\+?1?\d{10}',
            r'\+?1?\d{3}[-.\s]\d{3}[-.\s]\d{4}',
            r'\+?1?\(\d{3}\)\s*\d{3}[-.\s]\d{4}'
        ]
        
        phone = None
        for pattern in phone_patterns:
            phone_match = re.search(pattern, combined_message)
            if phone_match:
                phone = phone_match.group(0)
                break
        
        logger.info(f"Extracted phone: {phone}")
        
        return {"email": email, "phone": phone}
    
    def format_ticket_description(self, context: ConversationContext) -> str:
        """Format ticket description from conversation context."""
        # Get the initial issue (first user message that's not about email/contact)
        initial_issue = next(
            (msg.content for msg in context.messages 
             if msg.role == "user" and not any(x in msg.content.lower() for x in ["@", "email", "gmail", "yahoo"])),
            "No clear issue stated"
        )
        
        # Get user's contact info from context metadata
        contact_info = {
            "email": context.metadata.get("email", "Not provided"),
            "phone": context.metadata.get("phone", "Not provided")
        }
        
        # Format in HTML for better readability
        description = f"""
<h2>Customer Issue</h2>
<p>{initial_issue}</p>

<h2>Contact Information</h2>
<p>Email: {contact_info["email"]}</p>
<p>Phone: {contact_info["phone"]}</p>

<h2>Source</h2>
<p>Voice Assistant Call</p>

<h2>Conversation History</h2>
<pre>
{self._format_conversation_history(context.messages)}
</pre>
"""
        return description.strip()

    def _format_conversation_history(self, messages: List[Message]) -> str:
        """Format the conversation history with timestamps."""
        formatted = []
        for msg in messages:
            timestamp = msg.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            role = "Customer" if msg.role == "user" else "AI Assistant"
            formatted.append(f"[{timestamp}] {role}: {msg.content}")
        return "\n".join(formatted) 