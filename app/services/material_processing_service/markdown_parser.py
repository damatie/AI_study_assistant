# app/services/material_processing_service/markdown_parser.py

import re
import logging
from typing import List, Dict, Optional

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


def extract_topics_list_from_markdown(markdown_content: str) -> List[Dict[str, str]]:
    """
    Extract all topics from markdown content for assessment generation
    
    Args:
        markdown_content: The full markdown content
        
    Returns:
        List of topics with their names and content
    """
    topics = []
    
    try:
        # Find all topic sections marked with TOPIC_START/TOPIC_END
        topic_pattern = r"<!-- TOPIC_START: (.*?) -->(.*?)<!-- TOPIC_END: \1 -->"
        matches = re.findall(topic_pattern, markdown_content, re.DOTALL)
        
        for topic_id, content in matches:
            # Extract topic name from the content
            name_match = re.search(r"### (.*?):", content)
            topic_name = name_match.group(1).strip() if name_match else topic_id
            
            topics.append({
                "id": topic_id,
                "name": topic_name,
                "content": content.strip()
            })
            
        # If no marked topics found, try to extract from headers
        if not topics:
            header_pattern = r"### (.*?)\n(.*?)(?=###|\Z)"
            matches = re.findall(header_pattern, markdown_content, re.DOTALL)
            
            for i, (name, content) in enumerate(matches):
                # Skip certain sections that aren't topics
                skip_sections = [
                    "complete overview", "what you'll master", "content map", 
                    "why this matters", "learning path", "practice", "quick reference",
                    "connections", "completion checklist"
                ]
                
                if not any(skip in name.lower() for skip in skip_sections):
                    topics.append({
                        "id": f"topic-{i+1}",
                        "name": name.strip(),
                        "content": content.strip()
                    })
        
        logger.info(f"Extracted {len(topics)} topics from markdown")
        return topics
        
    except Exception as e:
        logger.exception(f"Error extracting topics list: {str(e)}")
        return []


def extract_metadata_from_markdown(markdown_content: str) -> Dict[str, any]:
    """
    Extract metadata from markdown comments
    
    Args:
        markdown_content: The full markdown content
        
    Returns:
        Dictionary containing extracted metadata
    """
    metadata = {}
    
    try:
        # Extract content tags
        tags_match = re.search(r'<!-- CONTENT_TAGS: \[(.*?)\] -->', markdown_content)
        if tags_match:
            tags_str = tags_match.group(1)
            # Parse the tags (remove quotes and split by comma)
            tags = [tag.strip().strip('"\'') for tag in tags_str.split(',')]
            metadata['content_tags'] = tags
        
        # Extract difficulty level
        difficulty_match = re.search(r'<!-- DIFFICULTY_LEVEL: "(.*?)" -->', markdown_content)
        if difficulty_match:
            metadata['difficulty_level'] = difficulty_match.group(1)
        
        # Extract estimated read time
        time_match = re.search(r'<!-- ESTIMATED_READ_TIME: "(.*?)" -->', markdown_content)
        if time_match:
            metadata['estimated_read_time'] = time_match.group(1)
        
        # Extract topics covered
        topics_match = re.search(r'<!-- TOPICS_COVERED: \[(.*?)\] -->', markdown_content)
        if topics_match:
            topics_str = topics_match.group(1)
            topics = [topic.strip().strip('"\'') for topic in topics_str.split(',')]
            metadata['topics_covered'] = topics
        
        # Extract assessment questions count
        questions_match = re.search(r'<!-- ASSESSMENT_QUESTIONS_AVAILABLE: (\d+) -->', markdown_content)
        if questions_match:
            metadata['assessment_questions_available'] = int(questions_match.group(1))
        
        logger.info(f"Extracted metadata: {metadata}")
        return metadata
        
    except Exception as e:
        logger.exception(f"Error extracting metadata: {str(e)}")
        return {}


def get_markdown_title(markdown_content: str) -> Optional[str]:
    """
    Extract the main title from markdown content
    
    Args:
        markdown_content: The full markdown content
        
    Returns:
        The main title or None if not found
    """
    try:
        # Look for the first # heading
        title_match = re.search(r'^# (.+)$', markdown_content, re.MULTILINE)
        if title_match:
            return title_match.group(1).strip()
        
        return None
        
    except Exception as e:
        logger.exception(f"Error extracting title: {str(e)}")
        return None


def extract_formulas_from_markdown(markdown_content: str) -> List[Dict[str, str]]:
    """
    Extract LaTeX formulas from markdown content
    
    Args:
        markdown_content: The full markdown content
        
    Returns:
        List of formulas with context
    """
    formulas = []
    
    try:
        # Find all LaTeX formulas (both inline and display)
        formula_patterns = [
            r'\$\$(.*?)\$\$',  # Display math
            r'\$(.*?)\$'       # Inline math
        ]
        
        for pattern in formula_patterns:
            matches = re.findall(pattern, markdown_content, re.DOTALL)
            for match in matches:
                formula = match.strip()
                if formula and len(formula) > 2:  # Skip very short matches
                    formulas.append({
                        "expression": formula,
                        "type": "display" if pattern.startswith(r'\$\$') else "inline"
                    })
        
        logger.info(f"Extracted {len(formulas)} formulas from markdown")
        return formulas
        
    except Exception as e:
        logger.exception(f"Error extracting formulas: {str(e)}")
        return []


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
