# app/core/genai_client.py
import asyncio
import logging
from typing import Optional, Dict, Any, List, Union
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions
from app.core.config import settings

# Configure logging
logger = logging.getLogger(__name__)

# Initialize Gemini API
genai.configure(api_key=settings.GOOGLE_API_KEY)

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

DEFAULT_MULTIMODAL_GENERATION_CONFIG: Dict[str, Any] = {
    "temperature": 0.65,
    "top_p": 0.85,
    "top_k": 40,
    "max_output_tokens": 16384,
}

FALLBACK_TEXT_GENERATION_CONFIG: Dict[str, Any] = {
    "temperature": 0.6,
    "top_p": 0.85,
    "top_k": 40,
    "max_output_tokens": 16384,
}

class GeminiClientWithRetry:
    """Enhanced Gemini client with retry logic and error handling."""
    
    # Default configurations
    DEFAULT_GENERATION_CONFIG = DEFAULT_MULTIMODAL_GENERATION_CONFIG.copy()
    
    DEFAULT_SAFETY_SETTINGS = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    ]
    
    def __init__(self, model_name: str = DEFAULT_GEMINI_MODEL):
        self.model_name = model_name
        self.model = genai.GenerativeModel(model_name)
        self.max_retries = 3
        self.base_delay = 1.0  # Base delay in seconds
        self.max_delay = 60.0  # Maximum delay in seconds
        
    async def generate_content_async(
        self, 
        prompt: Union[str, List[Dict[str, Any]]], 
        generation_config: Optional[Dict[str, Any]] = None,
        safety_settings: Optional[List[Dict[str, Any]]] = None
    ) -> Any:
        """
        Generate content with retry logic and comprehensive error handling.
        
        Args:
            prompt: The input prompt (string) or multimodal content (list)
            generation_config: Optional generation configuration
            safety_settings: Optional safety settings
            
        Returns:
            Generated response
            
        Raises:
            Exception: If all retries are exhausted
        """
        # Use defaults if not provided
        generation_config = generation_config or self.DEFAULT_GENERATION_CONFIG.copy()
        safety_settings = safety_settings or self.DEFAULT_SAFETY_SETTINGS.copy()
        
        last_exception = None
        
        for attempt in range(self.max_retries + 1):
            try:
                logger.info(f"Attempting Gemini API call (attempt {attempt + 1}/{self.max_retries + 1})")
                
                # Make the API call
                response = await self.model.generate_content_async(
                    prompt,
                    generation_config=generation_config,
                    safety_settings=safety_settings
                )
                
                # Validate response
                self._validate_response(response)
                
                logger.info("Gemini API call successful")
                return response
                
            except (google_exceptions.InternalServerError, 
                    google_exceptions.ResourceExhausted,
                    google_exceptions.DeadlineExceeded,
                    google_exceptions.ServiceUnavailable) as e:
                last_exception = e
                
                if attempt < self.max_retries:
                    delay = self._calculate_delay(e, attempt)
                    logger.warning(f"Gemini API {type(e).__name__} (attempt {attempt + 1}): {str(e)}")
                    logger.info(f"Retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
                    continue
                else:
                    logger.error(f"All retry attempts exhausted for {type(e).__name__}")
                    break
                    
            except (google_exceptions.InvalidArgument, google_exceptions.PermissionDenied) as e:
                # Don't retry for these errors
                last_exception = e
                logger.error(f"Gemini API {type(e).__name__}: {str(e)}")
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
        self._raise_user_friendly_error(last_exception)
    
    def _validate_response(self, response: Any) -> None:
        """Validate the API response."""
        if not response or not hasattr(response, 'text'):
            raise ValueError("Invalid response from Gemini API")
        
        if not response.text or response.text.strip() == "":
            raise ValueError("Empty response from Gemini API")
    
    def _calculate_delay(self, exception: Exception, attempt: int) -> float:
        """Calculate retry delay based on exception type and attempt number."""
        if isinstance(exception, google_exceptions.ResourceExhausted):
            # Longer delay for quota issues
            return min(self.base_delay * (3 ** attempt), self.max_delay)
        else:
            # Standard exponential backoff
            return min(self.base_delay * (2 ** attempt), self.max_delay)
    
    def _raise_user_friendly_error(self, last_exception: Exception) -> None:
        """Raise a user-friendly error message based on the exception type."""
        error_msg = f"Gemini API failed after {self.max_retries + 1} attempts. Last error: {str(last_exception)}"
        logger.error(error_msg)
        
        # Provide user-friendly error messages
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


# Global client instance
_gemini_client: Optional[GeminiClientWithRetry] = None


def get_gemini_model() -> GeminiClientWithRetry:
    """Return the shared Gemini client with retry/backoff handling."""
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = GeminiClientWithRetry()
    return _gemini_client
