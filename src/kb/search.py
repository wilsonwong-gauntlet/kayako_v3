"""Knowledge base search functionality using OpenAI embeddings."""

import os
from typing import List, Optional, Dict, Tuple
import numpy as np
from openai import AsyncOpenAI
from dotenv import load_dotenv
import json
import logging

from ..interfaces import Article
from .storage import EmbeddingStorage

# Load environment variables
load_dotenv()

# Initialize OpenAI client
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Configure logging
logger = logging.getLogger(__name__)

class KBSearchEngine:
    """Search engine for knowledge base articles."""
    
    def __init__(self):
        """Initialize the search engine."""
        self.storage = EmbeddingStorage()  # Let it use DATABASE_URL from env
        self.initialized = False
    
    async def initialize(self):
        """Initialize the search engine."""
        if self.initialized:
            return
            
        try:
            logger.info("Initializing KB search engine")
            
            # Initialize storage
            await self.storage.initialize()
            
            self.initialized = True
            
        except Exception as e:
            logger.error(f"Error initializing KB search engine: {str(e)}")
            raise
    
    async def _get_embedding(self, text: str) -> List[float]:
        """Get embedding for a piece of text."""
        try:
            response = await client.embeddings.create(
                model="text-embedding-3-small",
                input=text,
                encoding_format="float"
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"Error getting embedding: {str(e)}")
            raise
    
    def _calculate_similarity(self, embedding1: List[float], embedding2: List[float]) -> float:
        """Calculate cosine similarity between two embeddings."""
        # Convert to numpy arrays for efficient calculation
        a = np.array(embedding1)
        b = np.array(embedding2)
        
        # Calculate cosine similarity
        return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))
    
    async def search(self, query: str, max_results: int = 3) -> List[Tuple[Article, float]]:
        """
        Search for articles relevant to the query.
        
        Args:
            query: Search query
            max_results: Maximum number of results to return
        
        Returns:
            List of (article, relevance_score) tuples
        """
        try:
            logger.info(f"Searching for articles matching query: {query}")
            
            # Initialize if not done yet
            if not self.initialized:
                await self.initialize()
            
            # Get query embedding
            query_embedding = await self._get_embedding(query)
            
            # Find similar articles using vector similarity search with high threshold
            similar_articles = await self.storage.find_similar(
                query_embedding,
                limit=max_results,
                similarity_threshold=0.3  # Lower threshold for more matches
            )
            
            # Convert to list of (Article, score) tuples
            results = []
            for article_id, similarity in similar_articles:
                metadata = await self.storage.get_metadata(article_id)
                if metadata:
                    article = Article(
                        id=article_id,
                        title=metadata.get("title", ""),
                        content=metadata.get("content", ""),
                        tags=metadata.get("tags", []),
                        category=metadata.get("category", "General")
                    )
                    results.append((article, similarity))
                    logger.info(f"Found article: {article.title} (score: {similarity:.3f})")
            
            logger.info(f"Found {len(results)} relevant articles")
            return results
            
        except Exception as e:
            logger.error(f"Error in search: {str(e)}")
            return []
    
    async def generate_summary(self, article: Article, query: str) -> str:
        """
        Generate a concise, relevant summary of an article based on the query.
        
        Args:
            article: Article to summarize
            query: Original search query for context
        
        Returns:
            Concise summary focused on relevant information
        """
        system_prompt = """You are a helpful customer service AI that creates concise, relevant summaries of knowledge base articles.
Focus on the information that is most relevant to the user's query.
Keep the summary clear and suitable for voice responses (2-3 sentences).
Include any specific steps or requirements if they are relevant."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"""Query: {query}

Article Title: {article.title}

Article Content:
{article.content}

Create a concise, relevant summary focusing on answering the query."""}
        ]
        
        response = await client.chat.completions.create(
            model="gpt-4-turbo-preview",
            messages=messages,
            temperature=0.7,
            max_tokens=150  # Keep summaries concise
        )
        
        return response.choices[0].message.content
    
    async def search_and_summarize(self, query: str, max_results: int = 1) -> Optional[str]:
        """
        Search for articles and generate a relevant summary.
        
        Args:
            query: Search query
            max_results: Maximum number of results to summarize
        
        Returns:
            Summarized response or None if no relevant articles found
        """
        try:
            # Search for relevant articles
            results = await self.search(query, max_results=max_results)
            
            if not results:
                return None
            
            # Get the most relevant article
            best_match, score = results[0]
            
            # Only use the article if it's reasonably relevant
            if score < 0.5:  # Lower threshold to match find_similar
                return None
            
            # Generate a summary
            summary = await self.generate_summary(best_match, query)
            return summary
            
        except Exception as e:
            logger.error(f"Error in search_and_summarize: {str(e)}")
            return None 