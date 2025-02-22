from typing import List, Optional, Dict
from pydantic import BaseModel, EmailStr, Field
from datetime import datetime
from dataclasses import dataclass, field
from abc import ABC, abstractmethod

class User(BaseModel):
    """Represents a Kayako user."""
    id: str
    email: str
    full_name: str
    phone: Optional[str] = None
    organization: Optional[str] = None
    role: str = "customer"
    locale: str = "en-US"
    time_zone: str = "UTC"

class Message(BaseModel):
    """Represents a conversation message."""
    id: str
    conversation_id: str
    content: str
    type: str
    creator: Optional[Dict] = None
    attachments: List[Dict] = []
    created_at: datetime
    updated_at: Optional[datetime] = None
    is_private: bool = False

@dataclass
class Article:
    """Represents a knowledge base article."""
    id: str
    title: str
    content: str
    tags: List[str] = field(default_factory=list)
    category: str = "General"

    @classmethod
    def from_api_response(cls, item: Dict[str, any]) -> 'Article':
        """Create an Article instance from an API response."""
        # Extract article ID
        article_id = str(item.get('data', {}).get('id', item.get('id', '')))
        
        # Get title from the response
        title = item.get('title', f"Article {article_id}")
        
        # Get content, falling back to snippet if full content not available
        content = item.get('content', item.get('snippet', title))
        
        # Extract tags
        tags = []
        for tag in item.get('tags', []):
            if isinstance(tag, dict):
                tag_id = str(tag.get('id', ''))
                if tag_id:
                    tags.append(tag_id)
            else:
                tags.append(str(tag))
        
        # Get category
        category = item.get('category', '')
        
        return cls(
            id=article_id,
            title=title,
            content=content,
            tags=tags,
            category=category
        )

class Ticket(BaseModel):
    """Represents a support ticket."""
    subject: str
    contents: str
    requester_id: int
    priority_id: Optional[int] = None  # Make truly optional
    type_id: Optional[int] = None      # Make truly optional
    tags: Optional[List[str]] = None
    channel: str = "MAIL"
    channel_id: int = 1
    channel_options: Dict = Field(default_factory=lambda: {"html": True})
    
    class Config:
        """Pydantic model configuration."""
        arbitrary_types_allowed = True

class KayakoAPI(ABC):
    """Abstract base class for Kayako API client."""

    @abstractmethod
    async def search_articles(self, query: str = '', limit: Optional[int] = None) -> List[Article]:
        """Search knowledge base articles."""
        pass
    
    async def create_ticket(self, ticket: Ticket) -> str:
        """Create a new support ticket."""
        # TODO: Implement real API call or mock data
        pass
    
    async def get_article(self, article_id: str) -> Optional[Article]:
        """Get a single article by ID."""
        # TODO: Implement real API call or mock data
        pass

    async def get_user(self, user_id: str) -> Optional[User]:
        """Get a user by ID."""
        # TODO: Implement real API call or mock data
        pass
    
    async def get_user_by_email(self, email: str) -> Optional[User]:
        """Get a user by email address."""
        # TODO: Implement real API call or mock data
        pass
    
    async def create_user(self, user: User) -> str:
        """Create a new user."""
        # TODO: Implement real API call or mock data
        pass
    
    async def update_user(self, user_id: str, user: User) -> bool:
        """Update an existing user."""
        # TODO: Implement real API call or mock data
        pass
    
    async def search_users(self, query: str) -> List[User]:
        """Search for users."""
        # TODO: Implement real API call or mock data
        pass

    async def get_messages(self, conversation_id: str, page: int = 1, per_page: int = 50) -> List[Message]:
        """Get messages for a conversation."""
        # TODO: Implement real API call or mock data
        pass
    
    async def create_message(self, conversation_id: str, message: Message) -> str:
        """Create a new message in a conversation."""
        # TODO: Implement real API call or mock data
        pass
    
    async def update_message(self, message_id: str, message: Message) -> bool:
        """Update an existing message."""
        # TODO: Implement real API call or mock data
        pass
    
    async def delete_message(self, message_id: str) -> bool:
        """Delete a message."""
        # TODO: Implement real API call or mock data
        pass 