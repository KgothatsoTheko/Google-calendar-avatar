import asyncio
import logging
from datetime import datetime, timedelta, date
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo

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

TZ = ZoneInfo("Asia/Tbilisi")
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


def parse_event_time(value):
    """Parse an event timestamp in the calendar's timezone."""
    dt = datetime.fromisoformat(value)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)

    return dt.astimezone(TZ)


def get_events():
    """Get recent and upcoming events, excluding stale events."""
    now = datetime.now(TZ)
    window_start = now - timedelta(hours=3)
    window_end = now + timedelta(hours=12)

    with calendar_lock:
        response = calendar.events().list(
            calendarId="primary",
            timeMin=window_start.isoformat(),
            timeMax=window_end.isoformat(),
            timeZone=TZ.key,
            singleEvents=True,
            orderBy="startTime",
            maxResults=250,
        ).execute()

    events = []

    for event in response.get("items", []):
        start_data = event["start"]
        end_data = event["end"]

        # All-day events use dates rather than timestamps.
        if "date" in start_data:
            event_date = date.fromisoformat(start_data["date"])

            # Keep all-day events starting around today,
            # not old all-day events spanning several months.
            if window_start.date() <= event_date <= window_end.date():
                events.append(event)

            continue

        start = parse_event_time(start_data["dateTime"])
        end = parse_event_time(end_data["dateTime"])

        starts_in_window = window_start <= start < window_end

        # Keep a normal event already in progress, but reject
        # long-running stale events that began months ago.
        currently_running = (
            start <= now < end
            and end - start <= timedelta(hours=12)
        )

        if starts_in_window or currently_running:
            events.append(event)

    logger.info(
        "Calendar checked at %s; found %d relevant events.",
        now.strftime("%Y-%m-%d %H:%M %Z"),
        len(events),
    )

    return events


def read_schedule():
    """Give the AI dated, timezone-aware calendar information."""
    now = datetime.now(TZ)
    lines = []

    for event in get_events():
        title = event.get("summary", "Untitled event")
        start_data = event["start"]

        if "date" in start_data:
            event_date = date.fromisoformat(start_data["date"])
            lines.append(
                f"ALL-DAY EVENT: {title}; "
                f"date: {event_date:%A, %d %B %Y}"
            )
            continue

        start = parse_event_time(start_data["dateTime"])
        end = parse_event_time(event["end"]["dateTime"])

        if start <= now < end:
            minutes = int((now - start).total_seconds() // 60)
            status = f"IN PROGRESS; started {minutes} minutes ago"
        elif start > now:
            minutes = max(
                1,
                int((start - now).total_seconds() // 60),
            )
            status = f"UPCOMING; starts in {minutes} minutes"
        else:
            status = "ENDED"

        lines.append(
            f"{title}; scheduled for "
            f"{start:%A, %d %B %Y at %H:%M %Z}; "
            f"ends at {end:%H:%M %Z}; {status}; "
            f"event ID: {event['id']}"
        )

    schedule = "\n".join(lines) if lines else "No relevant events found."

    return (
        f"CURRENT DATE AND TIME: "
        f"{now:%A, %d %B %Y at %H:%M %Z}\n"
        f"TIMEZONE: {TZ.key}\n"
        f"CALENDAR EVENTS:\n{schedule}"
    )


def move_event(event_id: str, new_start: str):
    """Move a timed event and preserve its duration."""
    start = datetime.fromisoformat(new_start)

    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError(
            "Include a timezone offset, e.g. "
            "2026-10-12T15:30:00+04:00."
        )

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

        old_start = parse_event_time(event["start"]["dateTime"])
        old_end = parse_event_time(event["end"]["dateTime"])
        duration = old_end - old_start

        event["start"]["dateTime"] = start.isoformat()
        event["end"]["dateTime"] = (start + duration).isoformat()
        event["start"]["timeZone"] = TZ.key
        event["end"]["timeZone"] = TZ.key

        calendar.events().update(
            calendarId="primary",
            eventId=event_id,
            body=event,
        ).execute()

    return f"Moved to {start.astimezone(TZ):%A, %d %B at %H:%M}."


# AI avatar
class CalendarAvatar(Agent):
    def __init__(self):
        super().__init__(
            instructions="""
You are the user's calendar, given a face.

Be dry, deadpan, direct, and concise.
Speak in one or two short sentences.

Use the exact current date and times provided by the
calendar tool. Never guess the date from memory.

Check the calendar before answering calendar questions.
Only refer to events actually listed in the tool response.
Distinguish upcoming, ongoing, and ended events.
Never assume the user is absent or personally late.
Use the rescheduling tool when asked to move an event.
Confirm a change only after the tool succeeds.
"""
        )

    @function_tool
    async def get_schedule(self) -> str:
        """Read the dated Google Calendar schedule."""
        return await asyncio.to_thread(read_schedule)

    @function_tool
    async def reschedule_event(
        self,
        event_id: str,
        new_start: str,
    ) -> str:
        """Move a timed event using an ISO timestamp with timezone."""
        return await asyncio.to_thread(move_event, event_id, new_start)


# Load voice activity detection once per worker.
server = AgentServer()


def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


# Automatic reminders
async def send_reminders(session: AgentSession):
    reminded = set()

    while True:
        try:
            events = await asyncio.to_thread(get_events)
            now = datetime.now(TZ)

            for event in events:
                value = event["start"].get("dateTime")

                if not value or event["id"] in reminded:
                    continue

                start = parse_event_time(value)
                seconds_left = (start - now).total_seconds()

                if 0 < seconds_left <= 300:
                    minutes = max(1, int((seconds_left + 59) // 60))

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

    await session.start(agent=CalendarAvatar(), room=ctx.room)

    try:
        schedule = await asyncio.to_thread(read_schedule)
    except Exception:
        logger.exception("Could not load the opening schedule")
        schedule = "Calendar unavailable. Do not invent events."

    await session.generate_reply(
        instructions=f"""
Current calendar data:
{schedule}

Open with one or two short, deadpan sentences.
Use the exact date and event times from this data.
Mention only events in progress or starting within 30 minutes.
Do not invent dates, events, or attendance.
If nothing is imminent, briefly state the current date and time.
"""
    )

    reminder_task = asyncio.create_task(send_reminders(session))

    async def stop_reminders():
        reminder_task.cancel()
        await asyncio.gather(reminder_task, return_exceptions=True)

    ctx.add_shutdown_callback(stop_reminders)


if __name__ == "__main__":
    cli.run_app(server)