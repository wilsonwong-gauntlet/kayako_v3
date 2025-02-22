"""Knowledge base search functionality using OpenAI embeddings."""

import os
from typing import List, Optional, Dict, Tuple
import numpy as np
from openai import AsyncOpenAI
from dotenv import load_dotenv
import json
import logging

from src.api.kayako.interfaces import Article
from .storage import EmbeddingStorage

# Load environment variables
load_dotenv()

# Initialize OpenAI client
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Add a stream handler if none exists
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(handler)

class KBSearchEngine:
    """Search engine for knowledge base articles."""
    
    def __init__(self):
        """Initialize the search engine."""
        logger.info("Initializing KBSearchEngine instance")
        self.storage = EmbeddingStorage()  # Let it use DATABASE_URL from env
        self.initialized = False
    
    async def initialize(self):
        """Initialize the search engine."""
        if self.initialized:
            logger.info("KBSearchEngine already initialized")
            return
            
        try:
            logger.info("Initializing KB search engine and storage")
            
            # Initialize storage
            await self.storage.initialize()
            
            # Check article count
            article_count = await self.storage.get_article_count()
            logger.info(f"[RAG] Knowledge base contains {article_count} articles")
            if article_count == 0:
                logger.warning("[RAG] No articles found in knowledge base!")
            
            self.initialized = True
            logger.info("KBSearchEngine initialization complete")
            
        except Exception as e:
            logger.error(f"Error initializing KB search engine: {str(e)}")
            raise
    
    async def _get_embedding(self, text: str) -> List[float]:
        """Get embedding for a piece of text."""
        try:
            logger.debug(f"Getting embedding for text: {text[:100]}...")
            response = await client.embeddings.create(
                model="text-embedding-3-small",
                input=text,
                encoding_format="float"
            )
            logger.debug("Successfully got embedding")
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
            logger.info(f"[RAG] Searching for articles matching query: {query}")
            
            # Initialize if not done yet
            if not self.initialized:
                logger.info("[RAG] First search call - initializing")
                await self.initialize()
            
            # Get query embedding
            logger.info("[RAG] Getting query embedding")
            query_embedding = await self._get_embedding(query)
            
            # Find similar articles using vector similarity search with high threshold
            logger.info("[RAG] Finding similar articles")
            similar_articles = await self.storage.find_similar(
                query_embedding,
                limit=max_results,
                similarity_threshold=0.3  # Lower threshold for more matches
            )
            
            # Convert to list of (Article, score) tuples
            results = []
            for article_id, similarity in similar_articles:
                logger.info(f"[RAG] Getting metadata for article {article_id}")
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
                    logger.info(f"[RAG] Found article: {article.title} (score: {similarity:.3f})")
            
            logger.info(f"[RAG] Search complete. Found {len(results)} relevant articles")
            return results
            
        except Exception as e:
            logger.error(f"[RAG] Error in search: {str(e)}")
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
        logger.info(f"[RAG] Generating summary for article: {article.title}")
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
        
        try:
            logger.info("[RAG] Calling GPT-4 to generate summary")
            response = await client.chat.completions.create(
                model="gpt-4-turbo-preview",
                messages=messages,
                temperature=0.7,
                max_tokens=150  # Keep summaries concise
            )
            summary = response.choices[0].message.content
            logger.info(f"[RAG] Generated summary: {summary}")
            return summary
        except Exception as e:
            logger.error(f"[RAG] Error generating summary: {str(e)}")
            raise
    
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
            logger.info(f"[RAG] Starting search_and_summarize for query: {query}")
            
            # Search for relevant articles
            results = await self.search(query, max_results=max_results)
            
            if not results:
                logger.info("[RAG] No relevant articles found")
                return None
            
            # Get the most relevant article
            best_match, score = results[0]
            logger.info(f"[RAG] Best match: {best_match.title} (score: {score:.3f})")
            
            # Only use the article if it's reasonably relevant
            if score < 0.5:  # Lower threshold to match find_similar
                logger.info(f"[RAG] Best match score {score:.3f} below threshold 0.5")
                return None
            
            # Generate a summary
            logger.info("[RAG] Generating summary for best match")
            summary = await self.generate_summary(best_match, query)
            logger.info("[RAG] Summary generation complete")
            return summary
            
        except Exception as e:
            logger.error(f"[RAG] Error in search_and_summarize: {str(e)}")
            return None 