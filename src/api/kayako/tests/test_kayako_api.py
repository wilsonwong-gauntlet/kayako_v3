import asyncio
import os
from dotenv import load_dotenv
from src.kayako_client import KayakoAPIClient
from src.interfaces import Article, Ticket

async def test_kayako_api():
    # Load environment variables
    load_dotenv()
    
    # Initialize client with credentials from environment variables
    client = KayakoAPIClient(
        base_url=os.getenv('KAYAKO_API_URL'),
        email=os.getenv('KAYAKO_EMAIL'),
        password=os.getenv('KAYAKO_PASSWORD')
    )
    
    print('\n1. Testing Article Search:')
    try:
        articles = await client.search_articles('help')
        print(f'Found {len(articles)} articles')
        if articles:
            print(f'First article: {articles[0].title}')
            
            # Print details of first few articles
            for i, article in enumerate(articles[:5]):
                print(f"\nArticle {i+1}:")
                print(f"Title: {article.title}")
                print(f"Category: {article.category}")
                print(f"Tags: {', '.join(article.tags) if article.tags else 'No tags'}")
    except Exception as e:
        print(f'Error searching articles: {e}')

    print('\n2. Testing Ticket Creation:')
    try:
        # Create a test ticket with a predefined requester ID
        ticket = Ticket(
            subject='Test Support Request',
            contents='This is a test ticket created via API to test functionality.',
            channel='MAIL',
            channel_id=1,
            priority_id=3,
            requester_id=309  # Using the agent ID we saw in the article responses
        )
        ticket_id = await client.create_ticket(ticket)
        print(f'Successfully created ticket with ID: {ticket_id}')
    except Exception as e:
        print(f'Error creating ticket: {e}')

if __name__ == '__main__':
    asyncio.run(test_kayako_api()) 