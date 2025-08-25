# app/core/genai_client.py
import asyncio
import logging
import time
from typing import Optional, Dict, Any
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions
from app.core.config import settings

# Configure logging
logger = logging.getLogger(__name__)

# Configure the API
genai.configure(api_key=settings.GOOGLE_API_KEY)

class GeminiClientWithRetry:
    """Enhanced Gemini client with retry logic and error handling"""
    
    def __init__(self, model_name: str = "gemini-2.5-flash"):
        self.model_name = model_name
        self.model = genai.GenerativeModel(model_name)
        self.max_retries = 3
        self.base_delay = 1.0  # Base delay in seconds
        self.max_delay = 60.0  # Maximum delay in seconds
        
    async def generate_content_async(
        self, 
        prompt: str, 
        generation_config: Optional[Dict[str, Any]] = None,
        safety_settings: Optional[Dict[str, Any]] = None
    ) -> Any:
        """
        Generate content with retry logic and comprehensive error handling
        
        Args:
            prompt: The input prompt
            generation_config: Optional generation configuration
            safety_settings: Optional safety settings
            
        Returns:
            Generated response
            
        Raises:
            Exception: If all retries are exhausted
        """
        last_exception = None
        
        # Default generation config for better reliability
        if generation_config is None:
            generation_config = {
                "temperature": 0.7,
                "top_p": 0.8,
                "top_k": 40,
                "max_output_tokens": 8192,
            }
        
        # Default safety settings
        if safety_settings is None:
            safety_settings = [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            ]
        
        for attempt in range(self.max_retries + 1):
            try:
                logger.info(f"Attempting Gemini API call (attempt {attempt + 1}/{self.max_retries + 1})")
                
                # Make the API call
                response = await self.model.generate_content_async(
                    prompt,
                    generation_config=generation_config,
                    safety_settings=safety_settings
                )
                
                # Check if response is valid
                if not response or not hasattr(response, 'text'):
                    raise ValueError("Invalid response from Gemini API")
                
                if not response.text or response.text.strip() == "":
                    raise ValueError("Empty response from Gemini API")
                
                logger.info("Gemini API call successful")
                return response
                
            except google_exceptions.InternalServerError as e:
                last_exception = e
                logger.warning(f"Gemini API InternalServerError (attempt {attempt + 1}): {str(e)}")
                
                if attempt < self.max_retries:
                    delay = min(self.base_delay * (2 ** attempt), self.max_delay)
                    logger.info(f"Retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
                    continue
                else:
                    logger.error("All retry attempts exhausted for InternalServerError")
                    break
                    
            except google_exceptions.ResourceExhausted as e:
                last_exception = e
                logger.warning(f"Gemini API quota exhausted (attempt {attempt + 1}): {str(e)}")
                
                if attempt < self.max_retries:
                    # Longer delay for quota issues
                    delay = min(self.base_delay * (3 ** attempt), self.max_delay)
                    logger.info(f"Quota exhausted, waiting {delay} seconds before retry...")
                    await asyncio.sleep(delay)
                    continue
                else:
                    logger.error("All retry attempts exhausted for ResourceExhausted")
                    break
                    
            except google_exceptions.DeadlineExceeded as e:
                last_exception = e
                logger.warning(f"Gemini API timeout (attempt {attempt + 1}): {str(e)}")
                
                if attempt < self.max_retries:
                    delay = min(self.base_delay * (2 ** attempt), self.max_delay)
                    logger.info(f"Request timed out, retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
                    continue
                else:
                    logger.error("All retry attempts exhausted for DeadlineExceeded")
                    break
                    
            except google_exceptions.ServiceUnavailable as e:
                last_exception = e
                logger.warning(f"Gemini API service unavailable (attempt {attempt + 1}): {str(e)}")
                
                if attempt < self.max_retries:
                    delay = min(self.base_delay * (2 ** attempt), self.max_delay)
                    logger.info(f"Service unavailable, retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
                    continue
                else:
                    logger.error("All retry attempts exhausted for ServiceUnavailable")
                    break
                    
            except google_exceptions.InvalidArgument as e:
                # Don't retry for invalid arguments
                last_exception = e
                logger.error(f"Gemini API invalid argument: {str(e)}")
                break
                
            except google_exceptions.PermissionDenied as e:
                # Don't retry for permission issues
                last_exception = e
                logger.error(f"Gemini API permission denied: {str(e)}")
                break
                
            except Exception as e:
                last_exception = e
                logger.warning(f"Unexpected error with Gemini API (attempt {attempt + 1}): {str(e)}")
                
                if attempt < self.max_retries:
                    delay = min(self.base_delay * (2 ** attempt), self.max_delay)
                    logger.info(f"Unexpected error, retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
                    continue
                else:
                    logger.error("All retry attempts exhausted for unexpected error")
                    break
        
        # If we get here, all retries failed
        error_msg = f"Gemini API failed after {self.max_retries + 1} attempts. Last error: {str(last_exception)}"
        logger.error(error_msg)
        
        # Provide user-friendly error messages based on the type of error
        if isinstance(last_exception, google_exceptions.ResourceExhausted):
            raise Exception("AI service is currently experiencing high demand. Please try again in a few minutes.")
        elif isinstance(last_exception, google_exceptions.InternalServerError):
            raise Exception("AI service is temporarily unavailable. Please try again in a few moments.")
        elif isinstance(last_exception, google_exceptions.PermissionDenied):
            raise Exception("AI service configuration error. Please contact support.")
        elif isinstance(last_exception, google_exceptions.InvalidArgument):
            raise Exception("Invalid request format. Please try rephrasing your question.")
        else:
            raise Exception("AI service is temporarily unavailable. Please try again later.")

# Create a global instance
_gemini_client = GeminiClientWithRetry()

def get_gemini_model():
    """Get the enhanced Gemini client instance"""
    return _gemini_client

# Backward compatibility function
def get_gemini_model_legacy():
    """Legacy function that returns the basic model (for backward compatibility)"""
    return genai.GenerativeModel("gemini-2.0-flash-exp")
