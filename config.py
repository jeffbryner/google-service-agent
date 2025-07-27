import os
from dotenv import load_dotenv
import google.auth

load_dotenv()


class Config:
    AGENT_MODEL_NAME = "gemini-2.5-pro"
    TOOL_MODEL_NAME = "gemini-2.5-flash"
    CLIENT_ID = os.getenv("CLIENT_ID")
    CLIENT_SECRET = os.getenv("CLIENT_SECRET")
    REDIRECT_URI = "http://localhost:8000/callback"
    CREDENTIALS, PROJECT_ID = google.auth.default()

    os.environ["GOOGLE_CLOUD_PROJECT"] = PROJECT_ID
    os.environ["GOOGLE_CLOUD_LOCATION"] = "us-central1"  # change to suitable location
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"
