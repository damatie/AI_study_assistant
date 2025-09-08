# app/services/material_processing_service/markdown_parser.py

import re
import logging

logger = logging.getLogger(__name__)

def extract_topic_from_markdown(markdown_content: str, topic_name: str) -> str:
    """
    Extract specific topic content from markdown for targeted assessment generation
    
    Args:
        markdown_content: The full markdown content
        topic_name: The topic name to search for
        
    Returns:
        Extracted topic content or full content if topic not found
    """
    try:
        # Find topic sections in markdown using multiple patterns
        patterns = [
            # Pattern 1: ### Topic Name: ...
            rf"### .*{re.escape(topic_name)}.*?\n(.*?)(?=###|\Z)",
            # Pattern 2: ## Topic Name
            rf"## .*{re.escape(topic_name)}.*?\n(.*?)(?=##|\Z)",
            # Pattern 3: # Topic Name
            rf"# .*{re.escape(topic_name)}.*?\n(.*?)(?=#|\Z)",
        ]
        
        for pattern in patterns:
            match = re.search(pattern, markdown_content, re.DOTALL | re.IGNORECASE)
            if match:
                extracted_content = match.group(1).strip()
                logger.info(f"Found topic '{topic_name}' using pattern: {pattern[:50]}...")
                return extracted_content
        
        # If no specific topic found, return full content
        logger.info(f"Topic '{topic_name}' not found, returning full content")
        return markdown_content
        
    except Exception as e:
        logger.exception(f"Error extracting topic '{topic_name}': {str(e)}")
        return markdown_content


def clean_markdown_for_context(markdown_content: str) -> str:
    """
    Clean markdown content for use as context in AI services
    
    Args:
        markdown_content: The full markdown content
        
    Returns:
        Cleaned content suitable for AI context
    """
    try:
        # Remove HTML comments
        content = re.sub(r'<!--.*?-->', '', markdown_content, flags=re.DOTALL)
        
        # Remove collapsible section tags but keep content
        content = re.sub(r'<details>\s*<summary>.*?</summary>', '', content, flags=re.DOTALL)
        content = re.sub(r'</details>', '', content)
        
        # Clean up extra whitespace
        content = re.sub(r'\n\s*\n\s*\n', '\n\n', content)
        content = content.strip()
        
        return content
        
    except Exception as e:
        logger.exception(f"Error cleaning markdown: {str(e)}")
        return markdown_content
