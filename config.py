import os
from dotenv import load_dotenv

load_dotenv()
class Config:
    MODEL_NAME = "gemini-2.0-flash"
    CLIENT_ID = os.getenv("CLIENT_ID")
    CLIENT_SECRET = os.getenv("CLIENT_SECRET")
    REDIRECT_URI = "http://localhost:8000/callback"