from dotenv import load_dotenv
from google.adk.artifacts import InMemoryArtifactService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types
from google.adk.agents import LlmAgent

from google.adk.auth import AuthConfig, AuthCredential, AuthCredentialTypes, OAuth2Auth

from google.adk.tools.openapi_tool.openapi_spec_parser.openapi_toolset import (
    OpenAPIToolset,
)
from fastapi.openapi.models import OAuth2, OAuthFlowAuthorizationCode, OAuthFlows
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from datetime import datetime

try:
    from config import Config
except ImportError:
    print("Error: Could not import Config from configs.py. Please ensure it exists.")
    exit()


load_dotenv()

REDIRECT_URI = Config.REDIRECT_URI

try:
    client_id = Config.CLIENT_ID
    client_secret = Config.CLIENT_SECRET
    if not client_id or not client_secret:
        raise ValueError("CLIENT_ID or CLIENT_SECRET not found in environment/config.")
except (AttributeError, ValueError) as e:
    print(f"Error loading credentials from config: {e}")
    print("Please ensure configs.py and .env are set up correctly.")
    exit()


print("Setting up authentication...")
required_scopes = {
    "https://www.googleapis.com/auth/gmail.readonly": "gmail read scope",
    "https://www.googleapis.com/auth/gmail.send": "gmail send scope",
    "https://www.googleapis.com/auth/userinfo.profile": "user profile scope",
    "https://www.googleapis.com/auth/calendar.events": "calendar read/write events scope",
    "https://www.googleapis.com/auth/calendar.calendarlist": "calendar read scope",
}
auth_scheme = OAuth2(
    flows=OAuthFlows(
        authorizationCode=OAuthFlowAuthorizationCode(
            authorizationUrl="https://accounts.google.com/o/oauth2/auth",
            tokenUrl="https://oauth2.googleapis.com/token",
            scopes=required_scopes,
        )
    )
)
auth_credential = AuthCredential(
    auth_type=AuthCredentialTypes.OAUTH2,
    oauth2=OAuth2Auth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=REDIRECT_URI,
    ),
)
# load oauth spec
try:
    with open(".api_specs/open_api_oauth2.json", "r") as f:
        oauth_spec_str = f.read()
    oauth_api_toolset = OpenAPIToolset(
        spec_str=oauth_spec_str,
        spec_str_type="json",
        auth_scheme=auth_scheme,
        auth_credential=auth_credential,
    )

except FileNotFoundError:
    print("Error: Oauth2 OpenAPI spec file ('open_api_oauth2.json') not found.")
except Exception as e:
    print(f"Error loading oauth tools: {e}")


# Load Gmail tools
try:
    with open(".api_specs/open_api_gmail_spec.json", "r") as f:
        gmail_spec_str = f.read()
    gmail_api_toolset = OpenAPIToolset(
        spec_str=gmail_spec_str,
        spec_str_type="json",
        auth_scheme=auth_scheme,
        auth_credential=auth_credential,
        tool_filter=[
            "gmail_users_messages_list",
            "gmail_users_messages_get",
            "gmail_users_messages_send",
            "gmail_users_get_profile",
        ],
    )

except FileNotFoundError:
    print("Error: Gmail OpenAPI spec file ('open_api_gmail_spec.json') not found.")
except Exception as e:
    print(f"Error loading Gmail tools: {e}")

# Load Calendar tools
try:
    with open(".api_specs/open_api_calendar_spec.json", "r") as f:
        calendar_spec_str = f.read()
    calendar_api_toolset = OpenAPIToolset(
        spec_str=calendar_spec_str,
        spec_str_type="json",
        auth_scheme=auth_scheme,
        auth_credential=auth_credential,
        tool_filter=[
            "calendar_events_list",
            "calendar_events_insert",
            "calendar_events_get",
            "calendar_calendar_list_list",
        ],
    )

except FileNotFoundError:
    print(
        "Error: Calendar OpenAPI spec file ('open_api_calendar_spec.json') not found."
    )
except Exception as e:
    print(f"Error loading Calendar tools: {e}")


def update_time(callback_context: CallbackContext):
    # get current date time
    now = datetime.now()
    formatted_time = now.strftime("%Y-%m-%d %H:%M:%S")
    callback_context.state["_time"] = formatted_time


def get_current_date_time() -> dict:
    """
    Get the current date and time
    """
    return {"current_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


def create_raw_email_message(
    sender: str, recipient: str, subject: str, message_body: str
):
    """
    Creates an encoded email message suitable for using in the gmail REST API as the 'raw' parameter
    Args:
        sender (str):       The email address of the sender
        recipient (str):    The email address of the receipient
        subject (str):      The subject of the email
        message_body (str): The text body of the email

    Returns:
        A urlsafe base64 encoded string that can be used in the gmail api.
    """
    import base64
    from email.message import EmailMessage

    message = EmailMessage()
    message.set_content(message_body)
    message["To"] = recipient
    message["From"] = sender
    message["Subject"] = subject
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


gmail_agent = LlmAgent(
    model=Config.TOOL_MODEL_NAME,
    name="google_gmail_agent",
    description="Handles Gmail tasks like reading emails, sending emails, and checking user profiles.",
    instruction="""
    You handle tasks related to Gmail for email messages.
    Always retrieve the details of a message rather than just the message IDs. 

    Tool/Function hints: 
    - Use the gmail_users_messages_list function to get message IDs based on queries as prompted by the user
    - Use the gmail_users_messages_get function to get the details for a message ID and return answers to queries for the user
    - Use the gmail_users_messages_send function to send a message. The message must be passed as the 'raw' parameter in the REST API post.
    - You can use the create_raw_email_message function to get a raw email base64 encoded string that can be used with the gmail api when sending an email.    
    

    The current date/time is: {_time}                    
    """,
    tools=[gmail_api_toolset, create_raw_email_message, get_current_date_time],
    before_agent_callback=update_time,
)

calendar_agent = LlmAgent(
    model=Config.TOOL_MODEL_NAME,
    name="google_calendar_agent",
    description="Handles Calendar tasks like listing events, creating events, and getting event details.",
    instruction="""
    You handle queries related to Google Calendar. 
    - Never ask user to provide the calendarId, always set the calendarId in any function call to 'primary' to access the main Google Calendar.
    - Use the available tools to fulfill the user's request.
    - If you encounter an error, provide the *exact* error message so the user can debug.

    The current date/time is: {_time}                     
    """,
    tools=[calendar_api_toolset, get_current_date_time],
    before_agent_callback=update_time,
)


root_agent = LlmAgent(
    model=Config.AGENT_MODEL_NAME,
    name="task_root_agent",
    description="Acts as the main interface, routing tasks to Gmail or Calendar agents or answering general questions.",
    # Instruction updated to be clearer about delegation
    instruction="""Understand the user's request.
                - If the request relates to Gmail (reading/sending email, user profile), delegate the task to the 'google_gmail_agent'.
                - If the request relates to Google Calendar (listing/creating events), delegate the task to the 'google_calendar_agent'.
                - If the user asks a general question not related to Gmail or Calendar tools, answer it using your own knowledge.
                - If you are unsure which agent to use or the request is ambiguous, ask the user for clarification.
                - If a sub-agent reports an error, relay the exact error message to the user.
                
                The Current date/time is: {_time}                
                
                
                """,
    sub_agents=[gmail_agent, calendar_agent],
    before_agent_callback=update_time,
)
