"""Script to index Kayako articles into the vector database."""

import os
import sys
import asyncio
import logging
from dotenv import load_dotenv

# Add the src directory to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.kb.search import KBSearchEngine
from src.api.kayako.client import KayakoAPIClient
from src.kb.storage import EmbeddingStorage

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

async def index_articles():
    """Index all articles from Kayako into the vector database."""
    try:
        # Initialize API client
        api = KayakoAPIClient(
            base_url=os.getenv("KAYAKO_API_URL"),
            email=os.getenv("KAYAKO_EMAIL"),
            password=os.getenv("KAYAKO_PASSWORD")
        )
        
        # Initialize search engine and storage
        engine = KBSearchEngine()
        await engine.initialize()
        
        # Fetch all articles
        logger.info("Fetching articles from Kayako API...")
        articles = await api.search_articles()
        logger.info(f"Found {len(articles)} articles")
        
        # Index each article
        for article in articles:
            try:
                # Get embedding for article content
                text_to_embed = f"{article.title}\n\n{article.content}"
                embedding = await engine._get_embedding(text_to_embed)
                
                # Prepare metadata
                metadata = {
                    "title": article.title,
                    "content": article.content,
                    "category": article.category,
                    "tags": article.tags
                }
                
                # Save to database
                await engine.storage.save_embedding(
                    article_id=article.id,
                    embedding=embedding,
                    metadata=metadata
                )
                logger.info(f"Indexed article: {article.title}")
                
            except Exception as e:
                logger.error(f"Error indexing article {article.id}: {str(e)}")
                continue
        
        logger.info("Indexing complete!")
        
    except Exception as e:
        logger.error(f"Error during indexing: {str(e)}")
        raise

if __name__ == "__main__":
    load_dotenv()
    asyncio.run(index_articles()) 