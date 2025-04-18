from google.adk.events import Event
from google.adk.auth import AuthConfig
import asyncio

async def get_user_input(prompt: str) -> str:
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, input, prompt)
    except EOFError:
        print("\nInput stream closed unexpectedly. Exiting.")
        raise

def is_pending_auth_event(event: Event) -> bool:
    return (
        event.content and event.content.parts and event.content.parts[0]
        and event.content.parts[0].function_call
        and event.content.parts[0].function_call.name == 'adk_request_credential'
        and event.long_running_tool_ids
        and event.content.parts[0].function_call.id in event.long_running_tool_ids
    )

def get_function_call_id(event: Event) -> str:
    if ( event and event.content and event.content.parts and event.content.parts[0]
        and event.content.parts[0].function_call and event.content.parts[0].function_call.id ):
      return event.content.parts[0].function_call.id
    raise ValueError(f'Cannot get function call id from event {event}')

def get_function_call_auth_config(event: Event) -> AuthConfig:
    auth_config_dict = None
    try:
        auth_config_dict = event.content.parts[0].function_call.args.get('auth_config')
        if auth_config_dict and isinstance(auth_config_dict, dict):
            return AuthConfig.model_validate(auth_config_dict)
        else:
            raise ValueError("auth_config missing or not a dict in event args")
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as e:
        raise ValueError(f'Cannot get auth config from event {event}: {e}') from e