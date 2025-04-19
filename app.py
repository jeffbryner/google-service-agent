import asyncio
import os
import urllib.parse
from dotenv import load_dotenv
from datetime import datetime

from google.adk.artifacts import InMemoryArtifactService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types
from google.adk.agents import LlmAgent

from google.adk.auth import AuthConfig, AuthCredential, AuthCredentialTypes, OAuth2Auth

from google.adk.tools.openapi_tool.openapi_spec_parser.openapi_toolset import OpenAPIToolset
from fastapi.openapi.models import OAuth2, OAuthFlowAuthorizationCode, OAuthFlows

from helpers import is_pending_auth_event, get_function_call_id, get_function_call_auth_config, get_user_input

try:
    from config import Config 
except ImportError:
    print("Error: Could not import Config from configs.py. Please ensure it exists.")
    exit()

import warnings
warnings.filterwarnings("ignore")

load_dotenv()

MODEL_NAME = Config.MODEL_NAME
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
    "https://www.googleapis.com/auth/calendar.calendarlist": "calendar read scope"
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

print("Loading tools...")
gmail_tools = []
calendar_tools = []
try:
    with open("api_specs/open_api_gmail_spec.txt", "r") as f: 
        gmail_spec_str = f.read()
    gmail_api_toolset = OpenAPIToolset(
       spec_str=gmail_spec_str,
       spec_str_type='yaml', 
       auth_scheme=auth_scheme, auth_credential=auth_credential,
    )
    gmail_selected_tool_names = [
        'gmail_users_messages_list', 'gmail_users_messages_get',
        'gmail_users_messages_send', 'gmail_users_get_profile'
    ]
    all_gmail_tool_names = {tool.name for tool in gmail_api_toolset.get_tools()}
    gmail_tools = [tool for tool in gmail_api_toolset.get_tools() if tool.name in gmail_selected_tool_names]
    missing_gmail_tools = set(gmail_selected_tool_names) - all_gmail_tool_names
    if missing_gmail_tools: print(f"Warning: Gmail tools not found in spec: {missing_gmail_tools}")
except FileNotFoundError:
    print("Error: Gmail OpenAPI spec file ('open_api_gmail_spec.txt') not found.") # ADJUST FILENAME
except Exception as e:
    print(f"Error loading Gmail tools: {e}")

try:
    with open("api_specs/open_api_calendar_spec.txt", "r") as f:
        calendar_spec_str = f.read()
    calendar_api_toolset = OpenAPIToolset(
       spec_str=calendar_spec_str,
       spec_str_type='yaml', 
       auth_scheme=auth_scheme, auth_credential=auth_credential,
    )
    calendar_selected_tool_names = [
        'calendar_events_list', 'calendar_events_insert', 'calendar_events_get', 'calendar_calendar_list_list'
    ]
    all_calendar_tool_names = {tool.name for tool in calendar_api_toolset.get_tools()}
    calendar_tools = [tool for tool in calendar_api_toolset.get_tools() if tool.name in calendar_selected_tool_names]
    missing_calendar_tools = set(calendar_selected_tool_names) - all_calendar_tool_names
    if missing_calendar_tools: print(f"Warning: Calendar tools not found in spec: {missing_calendar_tools}")
except FileNotFoundError:
    print("Error: Calendar OpenAPI spec file ('open_api_calendar_spec.txt') not found.") # ADJUST FILENAME
except Exception as e:
    print(f"Error loading Calendar tools: {e}")

if not gmail_tools and not calendar_tools:
     print("Error: No valid tools could be loaded for either agent. Exiting.")
     exit()
elif not gmail_tools: print("Warning: No Gmail tools loaded. Gmail agent will be unavailable.")
elif not calendar_tools: print("Warning: No Calendar tools loaded. Calendar agent will be unavailable.")

today = datetime.today().strftime("%Y-%m-%d:%H:%M:%S")
timezone = "Asia/Colombo"

print("Defining agents...")
gmail_agent = LlmAgent(
    model=MODEL_NAME,
    name="google_gmail_agent",
    description="Handles Gmail tasks like reading emails, sending emails, and checking user profiles.",
    instruction=f"You handle queries related to Gmail. Do not ask any followup questions related to user ids, gmail ids etc. The special string `me` is used to refer to the authenticated user. You don't need to know the actual email address or user ID if you're making requests on behalf of the logged-in user. Use the available tools to fulfill the user's request. If you encounter an error, provide the *exact* error message so the user can debug. Today is {today} and the timezone is {timezone}",
    tools=gmail_tools
) if gmail_tools else None

calendar_agent = LlmAgent(
    model=MODEL_NAME,
    name="google_calendar_agent",
    description="Handles Calendar tasks like listing events, creating events, and getting event details.",
    instruction=f"You handle queries related to Google Calendar. Never ask user to provide the calendarId, users's main Google Calendar ID is usually just 'primary'. Use the available tools to fulfill the user's request. If you encounter an error, provide the *exact* error message so the user can debug. Today is {today} and the timezone is {timezone}",
    tools=calendar_tools
) if calendar_tools else None

sub_agents = [agent for agent in [gmail_agent, calendar_agent] if agent] 

router_agent = LlmAgent(
    model=MODEL_NAME,
    name="task_router_agent",
    description="Acts as the main interface, routing tasks to Gmail or Calendar agents or answering general questions.",
    # Instruction updated to be clearer about delegation
    instruction="""Understand the user's request.
                - If the request relates to Gmail (reading/sending email, user profile), delegate the task to the 'google_gmail_agent'.
                - If the request relates to Google Calendar (listing/creating events), delegate the task to the 'google_calendar_agent'.
                - If the user asks a general question not related to Gmail or Calendar tools, answer it using your own knowledge.
                - If you are unsure which agent to use or the request is ambiguous, ask the user for clarification.
                - If a sub-agent reports an error, relay the exact error message to the user.""",
    sub_agents=sub_agents
)

async def chat_loop():
    """Main asynchronous function orchestrating the continuous chat interaction."""
    print("Initializing services...")
    session_service = InMemorySessionService()
    artifacts_service = InMemoryArtifactService()

    session = session_service.create_session(
        state={}, app_name='my_chat_app', user_id='user_chat'
    )
    print(f"\nChat session started (ID: {session.id}).")
    print("Ask about Gmail, Calendar, or general questions. Type 'quit' to exit.")

    runner = Runner(
        app_name='my_chat_app',
        agent=router_agent, 
        artifact_service=artifacts_service,
        session_service=session_service
    )
    print(f"Runner configured with agent: {runner.agent.name}")

    while True:
        agent_response_text = ""
        auth_request_event_id = None
        auth_config = None

        try:
            query = await get_user_input("You: ")
            query_stripped = query.lower().strip()

            if query_stripped == 'quit':
                print("Exiting chat...")
                break
            if not query_stripped:
                continue

            print(f"Router Agent ({router_agent.name}): Processing query...")

            content = genai_types.Content(role='user', parts=[genai_types.Part(text=query)])

            events_async = runner.run_async(
                session_id=session.id,
                user_id=session.user_id,
                new_message=content
            )

            async for event in events_async:
                # print(f"DEBUG Event: {event}") 
                if is_pending_auth_event(event):
                    print("--> Authentication required by agent.")
                    try:
                         auth_request_event_id = get_function_call_id(event)
                         auth_config = get_function_call_auth_config(event)
                         break
                    except ValueError as e:
                         print(f"Error processing auth event: {e}")
                         auth_request_event_id = None
                         agent_response_text = "Sorry, there was an issue understanding the authentication request."
                         break # Exit event loop

                if event.content and event.content.parts:
                    last_part = event.content.parts[-1]
                    if last_part.text:
                        agent_response_text = last_part.text

            if auth_request_event_id and auth_config:
                try:
                    base_auth_uri = auth_config.exchanged_auth_credential.oauth2.auth_uri
                    if not base_auth_uri or not base_auth_uri.startswith("http"):
                        raise ValueError(f"Invalid base authorization URI received: {base_auth_uri}")

                    parsed_uri = urllib.parse.urlparse(base_auth_uri)
                    query_params = urllib.parse.parse_qs(parsed_uri.query)
                    query_params['redirect_uri'] = [REDIRECT_URI]
                    query_params['access_type'] = ['offline'] # Request refresh token
                    query_params['prompt'] = ['consent'] # Force consent screen
                    # Ensure scope is included if not already in base_auth_uri query
                    if 'scope' not in query_params and auth_config.exchanged_auth_credential.oauth2.scopes:
                         query_params['scope'] = [' '.join(auth_config.exchanged_auth_credential.oauth2.scopes)]

                    updated_query = urllib.parse.urlencode(query_params, doseq=True)
                    auth_request_uri = urllib.parse.urlunparse(
                        (parsed_uri.scheme, parsed_uri.netloc, parsed_uri.path,
                         parsed_uri.params, updated_query, parsed_uri.fragment)
                    )

                    print("\n--- User Action Required for Authentication ---")
                    auth_response_uri = await get_user_input(
                        f'1. Please open this URL in your browser to authorize:\n   {auth_request_uri}\n\n'
                        f'2. After authorization, copy the *entire* URL from your browser\'s address bar (it will contain a `code=` parameter).\n\n'
                        f'3. Paste the copied URL here and press Enter:\n\n> '
                    )
                    auth_response_uri = auth_response_uri.strip()

                    if not auth_response_uri:
                        print("Authentication aborted (no response URL provided).")
                        continue

                    auth_config.exchanged_auth_credential.oauth2.auth_response_uri = auth_response_uri
                    auth_config.exchanged_auth_credential.oauth2.redirect_uri = REDIRECT_URI

                    auth_content = genai_types.Content(
                        role='user',
                        parts=[
                            genai_types.Part(
                                function_response=genai_types.FunctionResponse(
                                    id=auth_request_event_id,
                                    name='adk_request_credential',
                                    response=auth_config.model_dump(),
                                )
                            )
                        ],
                    )

                    print(f"\nSubmitting authentication details back to the agent flow...")
                    events_async_after_auth = runner.run_async(
                        session_id=session.id,
                        user_id=session.user_id,
                        new_message=auth_content,
                    )

                    print(f"Agent: Processing after authentication...")
                    agent_response_text = "" 
                    async for event in events_async_after_auth:
                         # print(f"DEBUG Auth Event: {event}") # Debugging
                         if event.content and event.content.parts:
                            last_part = event.content.parts[-1]
                            if last_part.text:
                                agent_response_text = last_part.text 

                    if agent_response_text:
                        print(f"Agent: {agent_response_text}")
                    else:
                        print(f"Agent: Authentication seems complete. The requested action should be done.")

                except ValueError as ve:
                     print(f"\nAuthentication Flow Error: {ve}")
                     print("Agent: There was an issue during the authentication process. Please try again.")
                except Exception as e_auth:
                     print(f"\nAn unexpected error occurred during authentication: {e_auth}")
                    #  traceback.print_exc()
                     print("Agent: An unexpected error occurred during authentication. Please try again.")

            elif agent_response_text:
                 print(f"Agent: {agent_response_text}")
            elif not auth_request_event_id:
                 print(f"Agent: (No specific text response received, the action might be complete or I couldn't process the request)")


        except EOFError:
            print("\nInput stream closed. Exiting chat loop.")
            break
        except ValueError as ve:
            print(f"\nConfiguration or Data Error: {ve}")
        except Exception as e:
            print(f"\nAn unexpected error occurred in the main loop: {e}")
            print("Attempting to continue...")


if __name__ == '__main__':
    print("Starting chat application...")
    if not os.getenv("GOOGLE_API_KEY"):
        print("Warning: GOOGLE_API_KEY environment variable not set. Ensure it's configured if needed.")
    try:
        asyncio.run(chat_loop())
    except KeyboardInterrupt:
        print("\nChat interrupted by user.")
    except Exception as main_e:
        print(f"\nA critical error occurred: {main_e}")
    finally:
        print("Chat finished.")