"""Persistent storage for article embeddings using PostgreSQL + pgvector."""

import os
import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone
import asyncpg
from asyncpg import Pool
import numpy as np
from dotenv import load_dotenv
import json

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

class EmbeddingStorage:
    """Manages persistent storage of article embeddings using PostgreSQL + pgvector."""
    
    def __init__(self, dsn: Optional[str] = None):
        """
        Initialize the embedding storage.
        
        Args:
            dsn: PostgreSQL connection string. If None, uses DATABASE_URL env var.
        """
        self.dsn = dsn or os.getenv('DATABASE_URL')
        logger.debug(f"Using DSN: {self.dsn}")
        self.pool: Optional[Pool] = None
        
    async def initialize(self):
        """Initialize the database connection and create tables."""
        try:
            # Create connection pool
            self.pool = await asyncpg.create_pool(self.dsn)
            
            # Create extension and tables
            async with self.pool.acquire() as conn:
                # Enable pgvector extension
                await conn.execute('CREATE EXTENSION IF NOT EXISTS vector')
                
                # Create embeddings table
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS article_embeddings (
                        article_id TEXT PRIMARY KEY,
                        embedding vector(1536),  -- Dimension for text-embedding-3-small
                        metadata JSONB,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        model TEXT
                    )
                ''')
                
                # Create index for similarity search
                await conn.execute('''
                    CREATE INDEX IF NOT EXISTS article_embeddings_vector_idx 
                    ON article_embeddings 
                    USING ivfflat (embedding vector_cosine_ops)
                ''')
                
            logger.info("Initialized embedding storage with PostgreSQL + pgvector")
            
        except Exception as e:
            logger.error(f"Error initializing embedding storage: {str(e)}")
            raise
    
    async def save_embedding(
        self,
        article_id: str,
        embedding: List[float],
        metadata: Optional[Dict] = None
    ) -> None:
        """
        Save an embedding with metadata.
        
        Args:
            article_id: ID of the article
            embedding: The embedding vector
            metadata: Optional metadata about the embedding
        """
        if not self.pool:
            raise RuntimeError("Database connection not initialized")
            
        try:
            async with self.pool.acquire() as conn:
                # Convert embedding list to PostgreSQL vector format
                vector_str = f"[{','.join(map(str, embedding))}]"
                
                # Convert metadata to JSON string
                metadata_json = json.dumps(metadata) if metadata else '{}'
                
                await conn.execute('''
                    INSERT INTO article_embeddings (article_id, embedding, metadata, model)
                    VALUES ($1, $2::vector, $3::jsonb, $4)
                    ON CONFLICT (article_id) 
                    DO UPDATE SET 
                        embedding = $2::vector,
                        metadata = $3::jsonb,
                        model = $4,
                        created_at = CURRENT_TIMESTAMP
                ''', 
                article_id, 
                vector_str,
                metadata_json,
                "text-embedding-3-small"
                )
                
            logger.debug(f"Saved embedding for article {article_id}")
            
        except Exception as e:
            logger.error(f"Error saving embedding for article {article_id}: {str(e)}")
            raise
    
    async def get_embedding(self, article_id: str) -> Optional[List[float]]:
        """
        Get an embedding for an article.
        
        Args:
            article_id: ID of the article
            
        Returns:
            The embedding vector if found, None otherwise
        """
        if not self.pool:
            raise RuntimeError("Database connection not initialized")
            
        try:
            async with self.pool.acquire() as conn:
                result = await conn.fetchval(
                    'SELECT embedding::text FROM article_embeddings WHERE article_id = $1',
                    article_id
                )
                if result:
                    # Convert PostgreSQL vector string back to list of floats
                    # Remove brackets and split by commas
                    vector_str = result.strip('[]')
                    return [float(x) for x in vector_str.split(',')]
                return None
                
        except Exception as e:
            logger.error(f"Error getting embedding for article {article_id}: {str(e)}")
            return None
    
    async def delete_embedding(self, article_id: str) -> bool:
        """
        Delete an embedding.
        
        Args:
            article_id: ID of the article
            
        Returns:
            True if deleted, False otherwise
        """
        if not self.pool:
            raise RuntimeError("Database connection not initialized")
            
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute(
                    'DELETE FROM article_embeddings WHERE article_id = $1',
                    article_id
                )
                deleted = result.split()[-1]
                return int(deleted) > 0
                
        except Exception as e:
            logger.error(f"Error deleting embedding for article {article_id}: {str(e)}")
            return False
    
    async def get_all_embeddings(self) -> Dict[str, List[float]]:
        """
        Get all stored embeddings.
        
        Returns:
            Dictionary mapping article IDs to embeddings
        """
        if not self.pool:
            raise RuntimeError("Database connection not initialized")
            
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch('SELECT article_id, embedding FROM article_embeddings')
                return {row['article_id']: list(row['embedding']) for row in rows}
                
        except Exception as e:
            logger.error(f"Error getting all embeddings: {str(e)}")
            return {}
            
    async def find_similar(
        self,
        query_embedding: List[float],
        limit: int = 5,
        similarity_threshold: float = 0.5
    ) -> List[Tuple[str, float]]:
        """
        Find similar articles using vector similarity search.
        
        Args:
            query_embedding: Query embedding vector
            limit: Maximum number of results to return
            similarity_threshold: Minimum similarity score (0-1)
            
        Returns:
            List of (article_id, similarity_score) tuples
        """
        if not self.pool:
            raise RuntimeError("Database connection not initialized")
            
        try:
            # Convert query embedding to PostgreSQL vector format
            vector_str = f"[{','.join(map(str, query_embedding))}]"
            
            async with self.pool.acquire() as conn:
                # Use cosine similarity with pgvector
                results = await conn.fetch('''
                    SELECT 
                        article_id,
                        (1 - (embedding <=> $1::vector)) as similarity,
                        metadata
                    FROM article_embeddings
                    WHERE 1 - (embedding <=> $1::vector) > $2
                    ORDER BY similarity DESC
                    LIMIT $3
                ''',
                vector_str,
                similarity_threshold,
                limit
                )
                
                # Log search results for debugging
                logger.info(f"Found {len(results)} articles with similarity > {similarity_threshold}")
                for row in results:
                    metadata = row['metadata'] if row['metadata'] else {}
                    logger.info(f"Article {row['article_id']}: similarity={row['similarity']:.3f}, metadata={metadata}")
                
                return [(row['article_id'], row['similarity']) for row in results]
                
        except Exception as e:
            logger.error(f"Error finding similar embeddings: {str(e)}")
            raise 
    
    async def get_metadata(self, article_id: str) -> Optional[Dict]:
        """
        Get metadata for an article.
        
        Args:
            article_id: ID of the article
            
        Returns:
            Dictionary of metadata if found, None otherwise
        """
        if not self.pool:
            raise RuntimeError("Database connection not initialized")
            
        try:
            async with self.pool.acquire() as conn:
                result = await conn.fetchval(
                    'SELECT metadata FROM article_embeddings WHERE article_id = $1',
                    article_id
                )
                if result:
                    # Parse JSON string into dictionary
                    return json.loads(result) if isinstance(result, str) else result
                return None
                
        except Exception as e:
            logger.error(f"Error getting metadata for article {article_id}: {str(e)}")
            return None 