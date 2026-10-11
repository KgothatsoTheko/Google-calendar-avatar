
import asyncio
import logging
from datetime import datetime, timedelta
from threading import Lock

from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from livekit.agents import Agent, AgentServer, AgentSession, JobContext, cli, function_tool
from livekit.plugins import silero, synthesia

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("calendar-avatar")

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
AVATAR_ID = "8c1cc84a-1a48-40ca-a0ab-e25e699dfd60"
lock = Lock()

# Google Calendar authentication
creds = (
    Credentials.from_authorized_user_file("token.json", SCOPES)
    if __import__("os").path.exists("token.json")
    else None
)

if creds and creds.expired and creds.refresh_token:
    creds.refresh(Request())

if not creds or not creds.valid:
    flow = InstalledAppFlow.from_client_secrets_file(
        "credentials.json", SCOPES
    )
    creds = flow.run_local_server(port=0)

with open("token.json", "w") as f:
    f.write(creds.to_json())

calendar = build("calendar", "v3", credentials=creds, cache_discovery=False)


def get_events():
    """Fetch recent and upcoming calendar events."""
    now = datetime.now().astimezone()

    with lock:
        result = calendar.events().list(
            calendarId="primary",
            timeMin=(now - timedelta(hours=3)).isoformat(),
            timeMax=(now + timedelta(hours=12)).isoformat(),
            singleEvents=True,
            orderBy="startTime",
        ).execute()

    return result.get("items", [])


def read_schedule():
    """Create a readable schedule with event timing."""
    now = datetime.now().astimezone()
    lines = []

    for event in get_events():
        start_data = event["start"]
        title = event.get("summary", "Untitled event")

        if "dateTime" not in start_data:
            lines.append(f"{title}: all-day event")
            continue

        start = datetime.fromisoformat(start_data["dateTime"]).astimezone()
        end = datetime.fromisoformat(
            event["end"]["dateTime"]
        ).astimezone()

        if start <= now < end:
            mins = max(1, int((now - start).total_seconds() / 60))
            status = f"started {mins} minutes ago"
        elif start > now:
            mins = max(1, int((start - now).total_seconds() / 60))
            status = f"starts in {mins} minutes"
        else:
            status = "already ended"

        lines.append(
            f"{title}: {start:%H:%M}, {status}. ID: {event['id']}"
        )

    return f"Current time: {now:%H:%M}\n" + (
        "\n".join(lines) if lines else "No calendar events found."
    )


def move_event(event_id, new_start):
    """Move a timed event while preserving its duration."""
    start = datetime.fromisoformat(new_start)

    if start.tzinfo is None:
        raise ValueError("new_start must include a timezone offset.")

    with lock:
        event = calendar.events().get(
            calendarId="primary", eventId=event_id
        ).execute()

        if "dateTime" not in event["start"]:
            raise ValueError("All-day events cannot be moved by this tool.")

        old_start = datetime.fromisoformat(event["start"]["dateTime"])
        old_end = datetime.fromisoformat(event["end"]["dateTime"])

        event["start"]["dateTime"] = start.isoformat()
        event["end"]["dateTime"] = (
            start + (old_end - old_start)
        ).isoformat()

        calendar.events().update(
            calendarId="primary", eventId=event_id, body=event
        ).execute()

    return f"Moved to {start.astimezone():%A at %H:%M}."


class CalendarAvatar(Agent):
    def __init__(self):
        super().__init__(instructions="""
You are the user's calendar, given a face.
Be dry, deadpan, direct, and concise. Speak in one or two sentences.
Check the calendar before answering calendar questions.
State exactly how long ago a scheduled event started.
Never assume the user is absent or late merely because an event started.
Use the rescheduling tool when asked to move an event.
""")

    @function_tool
    async def get_schedule(self) -> str:
        """Read current and upcoming Google Calendar events."""
        return await asyncio.to_thread(read_schedule)

    @function_tool
    async def reschedule_event(self, event_id: str, new_start: str) -> str:
        """Move an event. Use ISO format with timezone, e.g.
        2026-10-12T15:30:00+04:00."""
        return await asyncio.to_thread(move_event, event_id, new_start)


async def send_reminders(session):
    reminded = set()

    while True:
        try:
            events = await asyncio.to_thread(get_events)
            now = datetime.now().astimezone()

            for event in events:
                value = event["start"].get("dateTime")
                if not value or event["id"] in reminded:
                    continue

                start = datetime.fromisoformat(value).astimezone()
                minutes = (start - now).total_seconds() / 60

                if 0 < minutes <= 5:
                    await session.say(
                        f"Reminder. {event.get('summary', 'Your event')} "
                        f"starts in {max(1, int(minutes))} minutes."
                    )
                    reminded.add(event["id"])

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Reminder check failed")

        await asyncio.sleep(30)


server = AgentServer()


@server.rtc_session()
async def entrypoint(ctx: JobContext):
    session = AgentSession(
        stt="cartesia/ink-2",
        llm="openai/gpt-4.1-mini",
        tts="cartesia/sonic-3",
        vad=silero.VAD.load(),
    )

    avatar = synthesia.AvatarSession(
        synthesia.AvatarConfig(avatar_ids=[AVATAR_ID])
    )

    # Start the avatar before the agent session.
    await avatar.start(session, room=ctx.room)
    await session.start(agent=CalendarAvatar(), room=ctx.room)

    reminder_task = asyncio.create_task(send_reminders(session))
    ctx.add_shutdown_callback(reminder_task.cancel)

    await session.generate_reply(
        instructions=(
            "Check the calendar and open with the current time. "
            "Mention any event in progress or starting soon. "
            "Keep it short and deadpan."
        )
    )


if __name__ == "__main__":
    cli.run_app(server)