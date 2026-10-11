# Google Calendar Avatar

A real-time AI avatar that reads your Google Calendar, reminds you of upcoming events, and lets you manage your schedule through natural conversation.

Instead of checking your calendar manually, talk to an interactive AI assistant that knows what's coming up and helps keep you accountable.

## Demo

<!-- Add a demo GIF or screenshot here. -->

<!-- Example: ![Google Calendar Avatar](assets/demo.gif) -->

<!-- Add a link to a video demonstration here when available. -->

## Features

* **Interactive AI avatar** — Have real-time voice conversations with a Synthesia avatar.
* **Calendar awareness** — Ask about recent and upcoming events.
* **Automated reminders** — Receive spoken reminders before scheduled events.
* **Event rescheduling** — Reschedule timed events through natural conversation.
* **Time awareness** — Understand event timing using current dates, times, and timezones.

## Tech Stack

* **Language:** Python
* **Real-time communication:** LiveKit Agents
* **Avatar:** Synthesia
* **AI and speech:** OpenAI, Cartesia
* **Voice activity detection:** Silero VAD
* **Calendar integration:** Google Calendar API

## Getting Started

### Prerequisites

* Python 3.13
* A Google account with Google Calendar
* A [LiveKit Cloud](https://cloud.livekit.io/) project
* Access to Synthesia interactive avatars
* The required AI and speech services configured through LiveKit

### 1. Clone the Repository

```bash
git clone https://github.com/KgothatsoTheko/Google-calendar-avatar.git
cd Google-calendar-avatar
```

### 2. Set Up the Environment

Create and activate a virtual environment on Windows:

```powershell
python -m venv .venv
.venv\Scripts\activate
```

Install the dependencies:

```powershell
python -m pip install -r requirements.txt
```

Install the [LiveKit CLI](https://docs.livekit.io/reference/developer-tools/livekit-cli/) if it isn't already installed.

### 3. Configure Google Calendar

1. Create or select a project in the [Google Cloud Console](https://console.cloud.google.com/).
2. Enable the Google Calendar API.
3. Configure OAuth consent and create a Desktop application OAuth client.
4. Download the credentials and save them as `credentials.json` in the project root.

The application prompts you to authorize calendar access on its first run and saves a `token.json` file for subsequent use.

### 4. Configure Environment Variables

Create a `.env` file in the project root:

```env
LIVEKIT_URL=wss://your-livekit-project.livekit.cloud
LIVEKIT_API_KEY=your-livekit-api-key
LIVEKIT_API_SECRET=your-livekit-api-secret
SYNTHESIA_API_KEY=your-synthesia-api-key
```

Replace the example values with your credentials and ensure the required AI and speech services are available through your LiveKit configuration.

**Never commit `.env`, `credentials.json`, or `token.json` to version control.**

### 5. Run the Agent

```powershell
lk agent dev agent.py --log-level INFO
```

Connect through the LiveKit Agent Console or a compatible frontend to interact with the avatar.

## Project Status

A working prototype exploring conversational AI, calendar integration, voice interaction, and real-time avatars.

## License

No license has been added yet.