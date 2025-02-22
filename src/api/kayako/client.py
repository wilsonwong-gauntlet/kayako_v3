import os
from typing import List, Optional, Dict, Any
import aiohttp
from tenacity import retry, stop_after_attempt, wait_exponential
from cachetools import TTLCache
from datetime import datetime, timedelta
import base64
from pydantic import BaseModel
import json
import logging
import re

from .interfaces import Article, Ticket, User, Message, KayakoAPI

logger = logging.getLogger(__name__)

class KayakoAuthManager:
    """Manages authentication for Kayako API."""
    
    def __init__(self, email: str, password: str, base_url: str):
        """Initialize with credentials."""
        self.email = email
        self.password = password
        self.base_url = base_url
        self.session_id = None
        self.csrf_token = None
    
    def _get_basic_auth_header(self) -> str:
        """Get basic auth header value."""
        credentials = f"{self.email}:{self.password}"
        return f"Basic {base64.b64encode(credentials.encode()).decode()}"
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def authenticate(self) -> str:
        """Authenticate with Kayako API and get session ID."""
        try:
            auth_url = f"{self.base_url}/users"
            
            # Initial headers without CSRF token
            headers = {
                "Authorization": self._get_basic_auth_header(),
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            
            logger.info(f"Attempting authentication at URL: {auth_url}")
            logger.info(f"Using headers: {headers}")
            
            async with aiohttp.ClientSession() as session:
                async with session.get(auth_url, headers=headers) as response:
                    response_text = await response.text()
                    logger.info(f"Auth response status: {response.status}")
                    logger.info(f"Auth response body: {response_text}")
                    
                    if response.status == 200:
                        data = json.loads(response_text)
                        self.session_id = data.get("session_id")
                        self.csrf_token = response.headers.get("X-CSRF-Token")
                        
                        if not self.session_id:
                            raise ValueError("No session ID in response")
                            
                        logger.info(f"Successfully authenticated with session ID: {self.session_id}")
                        logger.info(f"CSRF Token: {self.csrf_token}")
                        
                        return self.session_id
                    else:
                        raise aiohttp.ClientResponseError(
                            response.request_info,
                            response.history,
                            status=response.status,
                            message=f"Authentication failed: {response_text}"
                        )
                        
        except Exception as e:
            logger.error(f"Authentication error: {str(e)}")
            raise

    async def _get_headers(self) -> Dict[str, str]:
        """Get headers for API requests."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        # Add session ID if available
        if self.session_id:
            headers["X-Session-ID"] = self.session_id
            
        # Add CSRF token if available
        if self.csrf_token:
            headers["X-CSRF-Token"] = self.csrf_token
            
        return headers

    async def get_session_id(self) -> str:
        """Get a valid session ID, authenticating if necessary."""
        if not self.session_id:
            await self.authenticate()
        return self.session_id

class KayakoAPIClient(KayakoAPI):
    """Real implementation of Kayako API with Basic Auth and session management."""
    
    def __init__(self, base_url: str, email: str, password: str):
        self.base_url = base_url.rstrip('/')
        self.auth_manager = KayakoAuthManager(email, password, base_url)
        # Cache for article searches (5 minute TTL)
        self.search_cache = TTLCache(maxsize=100, ttl=300)
        # Cache for user lookups (1 minute TTL)
        self.user_cache = TTLCache(maxsize=100, ttl=60)
        # Cache for messages (30 second TTL)
        self.message_cache = TTLCache(maxsize=200, ttl=30)
    
    async def _get_headers(self) -> Dict[str, str]:
        """Get headers for API requests."""
        if not self.auth_manager.session_id:
            await self.auth_manager.authenticate()
        return await self.auth_manager._get_headers()
    
    async def _get_session_params(self) -> Dict[str, str]:
        """Get session ID as query parameter (alternative to header)."""
        if not self.auth_manager.session_id:
            await self.auth_manager.authenticate()
        return {'_session_id': self.auth_manager.session_id}
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def search_articles(self, query: str = '', limit: Optional[int] = None) -> List[Article]:
        """
        Get articles from the knowledge base.
        
        Args:
            query: Search query string
            limit: Maximum number of articles to return
            
        Returns:
            List of Article objects
        """
        # Check cache first
        cache_key = f"search:{query}:{limit}"
        if cache_key in self.search_cache:
            return self.search_cache[cache_key]
        
        async with aiohttp.ClientSession() as session:
            headers = await self._get_headers()
            url = f"{self.base_url}/articles.json"
            params = await self._get_session_params()
            params['include'] = 'contents,titles,tags,section'
            
            # Add search query if provided
            if query:
                params['q'] = query
            
            # Add limit if provided
            if limit is not None:
                params['per_page'] = limit
            
            logger.info(f"Fetching articles from: {url}")
            logger.debug(f"Headers: {headers}")
            logger.debug(f"Params: {params}")
            
            try:
                async with session.get(
                    url,
                    headers=headers,
                    params=params
                ) as response:
                    response.raise_for_status()
                    data = await response.json()
                    logger.debug(f"Raw API Response: {data}")
                    
                    articles = []
                    for item in data.get('data', []):
                        try:
                            article = await self.get_article(str(item.get('id', '')))
                            if article:
                                articles.append(article)
                                if limit and len(articles) >= limit:
                                    break
                        except Exception as e:
                            logger.error(f"Error processing article: {str(e)}")
                            continue
                    
                    # Cache the results
                    self.search_cache[cache_key] = articles
                    return articles
                    
            except aiohttp.ClientResponseError as e:
                logger.error(f"Articles API error: {e.status} - {e.message}")
                return []
            except Exception as e:
                logger.error(f"Unexpected error fetching articles: {str(e)}")
                return []
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def create_ticket(self, ticket: Ticket) -> str:
        """Create a new support ticket with retry logic."""
        async with aiohttp.ClientSession() as session:
            headers = await self._get_headers()
            
            # Prepare the ticket data according to Kayako's API format
            ticket_data = {
                'subject': ticket.subject,
                'contents': ticket.contents,
                'channel': 'MAIL',  # Match the curl example exactly
                'channel_id': 1,  # Match the curl example exactly
                'type_id': ticket.type_id or 1,  # Default to 1 if not set
                'priority_id': ticket.priority_id or 3,  # Default to 3 if not set
                'requester_id': ticket.requester_id,  # This should be set by now
                'channel_options': {
                    'html': True  # Allow HTML formatting in the contents
                }
            }
            
            url = f"{self.base_url}/cases"
            logger.info(f"Creating ticket at URL: {url}")
            logger.info(f"Headers: {headers}")
            logger.info(f"Ticket data: {ticket_data}")
            
            try:
                async with session.post(
                    url,
                    headers=headers,
                    json=ticket_data
                ) as response:
                    logger.info(f"Response status: {response.status}")
                    response_text = await response.text()
                    logger.info(f"Response body: {response_text}")
                    
                    response.raise_for_status()
                    data = await response.json()
                    # Access the ID from the nested data structure
                    return str(data['data']['id'])
            except aiohttp.ClientResponseError as e:
                logger.error(f"API error creating ticket: {e.status} - {e.message}")
                logger.error(f"Request URL: {url}")
                logger.error(f"Request headers: {headers}")
                logger.error(f"Request data: {ticket_data}")
                raise
            except Exception as e:
                logger.error(f"Unexpected error creating ticket: {str(e)}")
                raise
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def get_article_content(self, content_id: str) -> str:
        """Get article content by content ID."""
        async with aiohttp.ClientSession() as session:
            headers = await self._get_headers()
            params = await self._get_session_params()
            
            url = f"{self.base_url}/locale/fields/{content_id}.json"
            print(f"\n=== Making request to: {url} ===")
            print(f"Headers: {json.dumps(headers, indent=2)}")
            print(f"Params: {json.dumps(params, indent=2)}")
            
            try:
                async with session.get(
                    url,
                    headers=headers,
                    params=params
                ) as response:
                    response.raise_for_status()
                    data = await response.json()
                    print(f"\n=== Raw API Response for content {content_id} ===")
                    print(json.dumps(data, indent=2))
                    
                    # Get the translation directly from the response
                    content = data.get('data', {}).get('translation', '')
                    return content or 'No content available'
            except aiohttp.ClientResponseError as e:
                print(f"Error fetching article content: {e.status} - {e.message}")
                return f"Error fetching content: {e.status}"
            except Exception as e:
                print(f"Unexpected error fetching article content: {e}")
                return "Error fetching content"
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def get_article(self, article_id: str) -> Optional[Article]:
        """Get a single article by ID with full content."""
        async with aiohttp.ClientSession() as session:
            headers = await self._get_headers()
            params = await self._get_session_params()
            params['include'] = 'contents,titles,tags,section'
            
            url = f"{self.base_url}/articles/{article_id}.json"
            print(f"\n=== Making request to: {url} ===")
            print(f"Headers: {json.dumps(headers, indent=2)}")
            print(f"Params: {json.dumps(params, indent=2)}")
            
            try:
                async with session.get(
                    url,
                    headers=headers,
                    params=params
                ) as response:
                    response.raise_for_status()
                    data = await response.json()
                    print(f"\n=== Raw API Response for article {article_id} ===")
                    print(json.dumps(data, indent=2))
                    
                    item = data.get('data', {})
                    
                    # Get title from titles array
                    titles = item.get('titles', [])
                    title = ''
                    if titles:
                        title_content = await self.get_article_content(str(titles[0].get('id')))
                        title = title_content if title_content != 'No content available' else ''
                    
                    if not title:
                        # Fallback to slug if no title content
                        slugs = item.get('slugs', [])
                        for slug in slugs:
                            if slug.get('locale') == 'en-us':
                                title = slug.get('translation', '').replace('-', ' ').title()
                                break
                        if not title and slugs:
                            title = slugs[0].get('translation', '').replace('-', ' ').title()
                    
                    # Get content
                    contents = item.get('contents', [])
                    content = ''
                    if contents:
                        content = await self.get_article_content(str(contents[0].get('id')))
                    
                    # Get category from section
                    section = item.get('section', {})
                    section_slugs = section.get('slugs', [])
                    category = 'General'
                    if section_slugs:
                        for slug in section_slugs:
                            if slug.get('locale') == 'en-us':
                                category = slug.get('translation', '').replace('-', ' ').title()
                                break
                        if not category and section_slugs:
                            category = section_slugs[0].get('translation', '').replace('-', ' ').title()
                    
                    # Get tags
                    tags = [str(tag.get('id', '')) for tag in item.get('tags', [])]
                    
                    return Article(
                        id=str(item.get('id', '')),
                        title=title,
                        content=content,
                        tags=tags,
                        category=category
                    )
            except aiohttp.ClientResponseError as e:
                print(f"Error fetching article: {e.status} - {e.message}")
                return None
            except Exception as e:
                print(f"Unexpected error fetching article: {e}")
                return None
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def get_user(self, user_id: str) -> Optional[User]:
        """Get a user by ID with retry logic."""
        # Check cache first
        cache_key = f"user:{user_id}"
        if cache_key in self.user_cache:
            return self.user_cache[cache_key]

        async with aiohttp.ClientSession() as session:
            headers = await self._get_headers()
            
            async with session.get(
                f"{self.base_url}/users/{user_id}",
                headers=headers
            ) as response:
                if response.status == 404:
                    return None
                    
                response.raise_for_status()
                data = await response.json()
                
                user = User(
                    id=data['id'],
                    email=data['email'],
                    full_name=data['full_name'],
                    phone=data.get('phone'),
                    organization=data.get('organization'),
                    role=data.get('role', 'customer'),
                    locale=data.get('locale', 'en-US'),
                    time_zone=data.get('time_zone', 'UTC')
                )
                
                # Cache the result
                self.user_cache[cache_key] = user
                return user
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def create_user(self, user: User) -> str:
        """Create a new user with retry logic."""
        async with aiohttp.ClientSession() as session:
            headers = await self._get_headers()
            
            # Prepare user data according to Kayako's API format
            user_data = {
                'full_name': user.full_name,
                'role': user.role,  # Just send the role ID directly
                'locale': user.locale,  # Just send the locale ID directly
                'email': user.email,  # Send email directly
                'phone': user.phone if user.phone else None,  # Send phone directly if exists
                'is_enabled': True,
                'organization_id': user.organization if user.organization else None  # Send org ID directly if exists
            }
            
            logger.info(f"Creating user with data: {json.dumps(user_data)}")
            logger.info(f"Request headers: {headers}")
            
            try:
                async with session.post(
                    f"{self.base_url}/users",
                    headers=headers,
                    json=user_data
                ) as response:
                    response_text = await response.text()
                    logger.info(f"User creation response status: {response.status}")
                    logger.info(f"User creation response: {response_text}")
                    
                    response.raise_for_status()
                    data = json.loads(response_text)
                    
                    if not data.get('id'):
                        raise ValueError(f"Created user response missing ID: {response_text}")
                    
                    # Cache the new user
                    new_user = User(
                        id=data['id'],
                        email=user.email,
                        full_name=data['full_name'],
                        phone=user.phone,
                        organization=user.organization,
                        role=user.role,
                        locale=user.locale,
                        time_zone=data.get('time_zone')
                    )
                    
                    logger.info(f"Created new user: {new_user}")
                    
                    self.user_cache[f"user:{new_user.id}"] = new_user
                    self.user_cache[f"user_email:{new_user.email}"] = new_user
                    
                    return str(data['id'])
                    
            except Exception as e:
                logger.error(f"Error creating user: {str(e)}")
                logger.error(f"Request data: {user_data}")
                raise
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def update_user(self, user_id: str, user: User) -> bool:
        """Update an existing user with retry logic."""
        async with aiohttp.ClientSession() as session:
            headers = await self._get_headers()
            
            # Prepare update data
            update_data = {
                'email': user.email,
                'full_name': user.full_name,
                'phone': user.phone,
                'organization': user.organization,
                'role': user.role,
                'locale': user.locale,
                'time_zone': user.time_zone
            }
            
            async with session.put(
                f"{self.base_url}/users/{user_id}",
                headers=headers,
                json=update_data
            ) as response:
                if response.status == 404:
                    return False
                    
                response.raise_for_status()
                data = await response.json()
                
                # Update cache
                updated_user = User(**data)
                self.user_cache[f"user:{user_id}"] = updated_user
                self.user_cache[f"user_email:{updated_user.email}"] = updated_user
                
                return True
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def search_users(self, query: str) -> List[User]:
        """Search for users with retry logic."""
        async with aiohttp.ClientSession() as session:
            headers = await self._get_headers()
            params = {
                'query': query,
                **await self._get_session_params()  # Add session ID as query param
            }
            
            async with session.get(
                f"{self.base_url}/users",
                headers=headers,
                params=params
            ) as response:
                response.raise_for_status()
                data = await response.json()
                
                users = []
                for user_data in data.get('data', []):
                    # Get email ID from the first email in the emails array
                    email_id = user_data.get('emails', [{}])[0].get('id', '')
                    
                    # Get role ID from the nested role object
                    role = user_data.get('role', {}).get('id', 4)  # Default to 4 (customer)
                    
                    # Get locale ID from the nested locale object
                    locale = user_data.get('locale', {}).get('id', 2)  # Default to 2 (en-US)
                    
                    # Get organization ID from the nested organization object
                    organization = user_data.get('organization', {}).get('id') if user_data.get('organization') else None
                    
                    user = User(
                        id=user_data['id'],
                        email=str(email_id),  # Convert to string to ensure compatibility
                        full_name=user_data['full_name'],
                        phone=None,  # Phone is in a separate phones array
                        organization=organization,
                        role=role,
                        locale=locale,
                        time_zone=user_data.get('time_zone')
                    )
                    users.append(user)
                    
                    # Cache individual users
                    self.user_cache[f"user:{user.id}"] = user
                    if email_id:
                        self.user_cache[f"user_email:{email_id}"] = user
                
                return users
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def get_messages(self, conversation_id: str, page: int = 1, per_page: int = 50) -> List[Message]:
        """Get messages for a conversation with retry logic."""
        # Check cache first
        cache_key = f"messages:{conversation_id}:{page}:{per_page}"
        if cache_key in self.message_cache:
            return self.message_cache[cache_key]

        async with aiohttp.ClientSession() as session:
            headers = await self._get_headers()
            params = {
                'page': page,
                'per_page': per_page,
                'sort': '-created_at'  # Sort by newest first
            }
            
            async with session.get(
                f"{self.base_url}/conversations/{conversation_id}/messages",
                headers=headers,
                params=params
            ) as response:
                response.raise_for_status()
                data = await response.json()
                
                messages = []
                for msg_data in data.get('data', []):
                    message = Message(
                        id=msg_data['id'],
                        conversation_id=conversation_id,
                        content=msg_data['content'],
                        type=msg_data['type'],
                        creator=msg_data.get('creator'),
                        attachments=msg_data.get('attachments', []),
                        created_at=datetime.fromisoformat(msg_data['created_at'].replace('Z', '+00:00')),
                        updated_at=datetime.fromisoformat(msg_data['updated_at'].replace('Z', '+00:00')) if msg_data.get('updated_at') else None,
                        is_private=msg_data.get('is_private', False)
                    )
                    messages.append(message)
                    
                    # Cache individual messages
                    self.message_cache[f"message:{message.id}"] = message
                
                # Cache the list of messages
                self.message_cache[cache_key] = messages
                return messages
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def create_message(self, conversation_id: str, message: Message) -> str:
        """Create a new message in a conversation with retry logic."""
        async with aiohttp.ClientSession() as session:
            headers = await self._get_headers()
            
            # Prepare message data
            message_data = {
                'content': message.content,
                'type': message.type,
                'is_private': message.is_private,
                'attachments': message.attachments
            }
            
            async with session.post(
                f"{self.base_url}/conversations/{conversation_id}/messages",
                headers=headers,
                json=message_data
            ) as response:
                response.raise_for_status()
                data = await response.json()
                
                # Create and cache the new message
                new_message = Message(
                    id=data['id'],
                    conversation_id=conversation_id,
                    content=data['content'],
                    type=data['type'],
                    creator=data.get('creator'),
                    attachments=data.get('attachments', []),
                    created_at=datetime.fromisoformat(data['created_at'].replace('Z', '+00:00')),
                    updated_at=None,
                    is_private=data.get('is_private', False)
                )
                
                # Cache the new message
                self.message_cache[f"message:{new_message.id}"] = new_message
                
                # Invalidate conversation messages cache
                for key in list(self.message_cache.keys()):
                    if key.startswith(f"messages:{conversation_id}:"):
                        del self.message_cache[key]
                
                return str(data['id'])
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def update_message(self, message_id: str, message: Message) -> bool:
        """Update an existing message with retry logic."""
        async with aiohttp.ClientSession() as session:
            headers = await self._get_headers()
            
            # Prepare update data
            update_data = {
                'content': message.content,
                'type': message.type,
                'is_private': message.is_private,
                'attachments': message.attachments
            }
            
            async with session.put(
                f"{self.base_url}/messages/{message_id}",
                headers=headers,
                json=update_data
            ) as response:
                if response.status == 404:
                    return False
                    
                response.raise_for_status()
                data = await response.json()
                
                # Update cache with the updated message
                updated_message = Message(
                    id=data['id'],
                    conversation_id=message.conversation_id,
                    content=data['content'],
                    type=data['type'],
                    creator=data.get('creator'),
                    attachments=data.get('attachments', []),
                    created_at=datetime.fromisoformat(data['created_at'].replace('Z', '+00:00')),
                    updated_at=datetime.fromisoformat(data['updated_at'].replace('Z', '+00:00')) if data.get('updated_at') else None,
                    is_private=data.get('is_private', False)
                )
                
                # Update message cache
                self.message_cache[f"message:{message_id}"] = updated_message
                
                # Invalidate conversation messages cache
                for key in list(self.message_cache.keys()):
                    if key.startswith(f"messages:{message.conversation_id}:"):
                        del self.message_cache[key]
                
                return True
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def delete_message(self, message_id: str) -> bool:
        """Delete a message with retry logic."""
        async with aiohttp.ClientSession() as session:
            headers = await self._get_headers()
            
            async with session.delete(
                f"{self.base_url}/messages/{message_id}",
                headers=headers
            ) as response:
                if response.status == 404:
                    return False
                
                response.raise_for_status()
                
                # Remove from cache
                if f"message:{message_id}" in self.message_cache:
                    message = self.message_cache[f"message:{message_id}"]
                    # Invalidate conversation messages cache
                    for key in list(self.message_cache.keys()):
                        if key.startswith(f"messages:{message.conversation_id}:"):
                            del self.message_cache[key]
                    del self.message_cache[f"message:{message_id}"]
                
                return True
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def get_user_by_email(self, email: str) -> Optional[User]:
        """Get a user by email with retry logic."""
        # Check cache first
        cache_key = f"user_email:{email}"
        if cache_key in self.user_cache:
            return self.user_cache[cache_key]

        async with aiohttp.ClientSession() as session:
            headers = await self._get_headers()
            params = {'email': email}
            
            try:
                logger.info(f"Looking up user by email: {email}")
                logger.info(f"Request headers: {headers}")
                logger.info(f"Request params: {params}")
                
                async with session.get(
                    f"{self.base_url}/users",
                    headers=headers,
                    params=params
                ) as response:
                    response_text = await response.text()
                    logger.info(f"User lookup response status: {response.status}")
                    logger.info(f"User lookup response: {response_text}")
                    
                    response.raise_for_status()
                    data = json.loads(response_text)
                    
                    # Check if we got any users back
                    users = data.get('data', [])
                    if not users:
                        logger.info(f"No user found for email: {email}")
                        return None
                    
                    # Get first matching user
                    user_data = users[0]
                    
                    # Create user object
                    user = User(
                        id=user_data['id'],
                        email=email,  # Use the email we searched with
                        full_name=user_data['full_name'],
                        phone=user_data.get('phones', [{}])[0].get('phone') if user_data.get('phones') else None,
                        organization=user_data.get('organization', {}).get('id'),
                        role=user_data.get('role', {}).get('id', 4),  # Default to customer role
                        locale=user_data.get('locale', {}).get('id', 2),  # Default to en-US
                        time_zone=user_data.get('time_zone')
                    )
                    
                    logger.info(f"Found user: {user}")
                    
                    # Cache the result
                    self.user_cache[cache_key] = user
                    return user
                    
            except Exception as e:
                logger.error(f"Error looking up user by email: {str(e)}")
                logger.error(f"Email: {email}")
                raise 