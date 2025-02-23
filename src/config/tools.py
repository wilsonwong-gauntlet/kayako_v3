"""Tool configurations for the OpenAI assistant."""

TOOLS = [
    {
        "type": "function",
        "name": "search_knowledge_base",
        "description": "Query a knowledge base to retrieve relevant info on a topic.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The user question or search query about AdvocateHub."
                }
            },
            "required": ["query"]
        }
    },
    {
        "type": "function",
        "name": "save_user_email",
        "description": "Save the user's email address when they provide it.",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "The email address provided by the user."
                }
            },
            "required": ["email"]
        }
    },
    {
        "type": "function",
        "name": "set_reason_for_calling",
        "description": "Set the user's reason for calling once they've clearly stated their issue.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "A clear, concise summary of why the user is calling."
                }
            },
            "required": ["reason"]
        }
    }
] 