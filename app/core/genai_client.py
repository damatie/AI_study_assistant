# app/core/genai_client.py
import google.generativeai as genai
from app.core.config import settings

genai.configure(api_key=settings.GOOGLE_API_KEY)
_model = genai.GenerativeModel("gemini-2.5-pro")

def get_gemini_model():
    return _model