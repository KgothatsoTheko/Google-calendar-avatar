import asyncio
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    cli,
    function_tool,
)
from livekit.plugins import silero, synthesia


# Configuration
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("calendar-avatar")

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
AVATAR_ID = "8c1cc84a-1a48-40ca-a0ab-e25e699dfd60"
VOICE_ID = "9626c31c-bec5-4cca-baa8-f8ba9e84c8bc"

calendar_lock = Lock()


# Google Calendar authentication
token_path = BASE_DIR / "token.json"
credentials_path = BASE_DIR / "credentials.json"

creds = (
    Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if token_path.exists()
    else None
)

if creds and creds.expired and creds.refresh_token:
    creds.refresh(Request())

if not creds or not creds.valid:
    flow = InstalledAppFlow.from_client_secrets_file(
        str(credentials_path), SCOPES
    )
    creds = flow.run_local_server(port=0)

token_path.write_text(creds.to_json(), encoding="utf-8")

calendar = build(
    "calendar",
    "v3",
    credentials=creds,
    cache_discovery=False,
)


def get_events():
    """Fetch recent and upcoming calendar events."""
    now = datetime.now().astimezone()

    with calendar_lock:
        response = calendar.events().list(
            calendarId="primary",
            timeMin=(now - timedelta(hours=3)).isoformat(),
            timeMax=(now + timedelta(hours=12)).isoformat(),
            singleEvents=True,
            orderBy="startTime",
        ).execute()

    return response.get("items", [])


def read_schedule():
    """Return calendar events with readable timing information."""
    now = datetime.now().astimezone()
    lines = []

    for event in get_events():
        title = event.get("summary", "Untitled event")
        start_data = event["start"]

        if "dateTime" not in start_data:
            lines.append(f"{title}: all-day event")
            continue

        start = datetime.fromisoformat(
            start_data["dateTime"]
        ).astimezone()

        end = datetime.fromisoformat(
            event["end"]["dateTime"]
        ).astimezone()

        if start <= now < end:
            minutes = int((now - start).total_seconds() // 60)
            status = f"started {minutes} minutes ago"
        elif start > now:
            minutes = max(
                1,
                int((start - now).total_seconds() // 60),
            )
            status = f"starts in {minutes} minutes"
        else:
            status = "already ended"

        lines.append(
            f"{title}: {start:%H:%M}, {status}; ID: {event['id']}"
        )

    return (
        f"Current local time: {now:%A, %H:%M}\n"
        + ("\n".join(lines) if lines else "No calendar events found.")
    )


def move_event(event_id: str, new_start: str):
    """Move a timed event while preserving its duration."""
    start = datetime.fromisoformat(new_start)

    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("Provide a timezone offset in new_start.")

    with calendar_lock:
        event = calendar.events().get(
            calendarId="primary",
            eventId=event_id,
        ).execute()

        if (
            "dateTime" not in event["start"]
            or "dateTime" not in event["end"]
        ):
            raise ValueError("All-day events cannot be moved.")

        old_start = datetime.fromisoformat(
            event["start"]["dateTime"]
        )
        old_end = datetime.fromisoformat(
            event["end"]["dateTime"]
        )

        event["start"]["dateTime"] = start.isoformat()
        event["end"]["dateTime"] = (
            start + (old_end - old_start)
        ).isoformat()

        calendar.events().update(
            calendarId="primary",
            eventId=event_id,
            body=event,
        ).execute()

    return f"Moved to {start.astimezone():%A at %H:%M}."


# Avatar
class CalendarAvatar(Agent):
    def __init__(self):
        super().__init__(
            instructions="""
You are the user's calendar, given a face.

Be dry, deadpan, direct, and concise.
Speak in one or two short sentences.
Check the calendar before answering calendar questions.
State accurately how long ago an event started.
Never assume the user is late or absent based only on a calendar.
Use the rescheduling tool when asked to move an event.
Confirm changes only after the tool succeeds.
"""
        )

    @function_tool
    async def get_schedule(self) -> str:
        """Read current and upcoming Google Calendar events."""
        return await asyncio.to_thread(read_schedule)

    @function_tool
    async def reschedule_event(
        self,
        event_id: str,
        new_start: str,
    ) -> str:
        """Move a timed event. Include a timezone offset, e.g.
        2026-10-12T15:30:00+04:00."""
        return await asyncio.to_thread(
            move_event, event_id, new_start
        )


# Preload VAD outside the active voice session.
server = AgentServer()


def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


async def send_reminders(session: AgentSession):
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
                seconds_left = (start - now).total_seconds()

                if 0 < seconds_left <= 300:
                    minutes = max(
                        1,
                        int((seconds_left + 59) // 60),
                    )

                    await session.say(
                        f"Reminder. "
                        f"{event.get('summary', 'Your event')} "
                        f"starts in {minutes} "
                        f"{'minute' if minutes == 1 else 'minutes'}.",
                        allow_interruptions=False,
                    )

                    reminded.add(event["id"])

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Calendar reminder failed")

        await asyncio.sleep(30)


@server.rtc_session()
async def entrypoint(ctx: JobContext):
    # Connect early instead of waiting for session startup to connect.
    await ctx.connect()

    session = AgentSession(
        stt="cartesia/ink-2",
        llm="openai/gpt-4.1-mini",
        tts=f"cartesia/sonic-3:{VOICE_ID}",
        vad=ctx.proc.userdata["vad"],
    )

    avatar = synthesia.AvatarSession(
        synthesia.AvatarConfig(avatar_ids=[AVATAR_ID])
    )

    logger.info("Starting Synthesia avatar...")
    await avatar.start(session, room=ctx.room)
    logger.info("Synthesia avatar joined.")

    await session.start(
        agent=CalendarAvatar(),
        room=ctx.room,
    )

    # Fetch the opening schedule in a background thread.
    try:
        schedule = await asyncio.to_thread(read_schedule)
    except Exception:
        logger.exception("Could not read the opening calendar")
        schedule = "Calendar unavailable. Do not invent any events."

    await session.generate_reply(
        instructions=f"""
Open with one or two short, dry, deadpan sentences.

Current calendar information:
{schedule}

Mention events currently in progress or starting within 30 minutes.
State the scheduled time and elapsed minutes accurately.
Do not claim the user is absent or personally late.
Do not recite the entire calendar.
"""
    )

    reminder_task = asyncio.create_task(send_reminders(session))

    # LiveKit 1.8.6 needs a regular callable here, not task.cancel itself.
    async def stop_reminders():
        reminder_task.cancel()
        await asyncio.gather(
            reminder_task,
            return_exceptions=True,
        )

    ctx.add_shutdown_callback(stop_reminders)


if __name__ == "__main__":
    cli.run_app(server)