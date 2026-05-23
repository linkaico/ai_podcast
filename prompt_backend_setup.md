No search needed for this — I have everything required. Let me write this as a clean, paste-ready Claude Code prompt.

***

````markdown
# AI Podcast Pipeline — Claude Code Setup Prompt

## Context
You are setting up an AI podcast pipeline on a VPS that is already running the OpenClaw multi-agent system. The goal is to build an async STT → LLM → TTS conversation pipeline that allows the host (Florian) to have a live, recorded conversation with a frontier AI model. The AI's voice is synthesized via ElevenLabs and routed into OBS as a separate audio track. There is no real-time constraint — a 5–15 second processing delay between turns is acceptable and will be edited out in post-production.

---

## Project Structure

Create the following directory structure under `~/openclaw/podcast/`:

```
podcast/
├── main.py                  # Entry point — push-to-talk conversation loop
├── pipeline/
│   ├── __init__.py
│   ├── stt.py               # Deepgram speech-to-text
│   ├── llm.py               # Frontier LLM call (Anthropic / OpenAI / Google)
│   ├── tts.py               # ElevenLabs text-to-speech streaming
│   └── memory.py            # Conversation history manager
├── config/
│   ├── settings.py          # API keys, model config, voice ID
│   └── prompts/
│       ├── base_system.txt  # Core AI podcaster persona
│       └── episodes/        # Per-episode system prompt overrides + RAG context
├── sessions/                # Auto-saved episode transcripts (JSON)
├── audio/
│   ├── input/               # Recorded mic clips (.wav)
│   └── output/              # AI response audio clips (.mp3)
├── requirements.txt
└── .env                     # All secrets — never commit this
```

---

## Dependencies

Create `requirements.txt` with the following:

```
anthropic>=0.50.0
openai>=1.70.0
google-generativeai>=0.8.0
deepgram-sdk>=3.10.0
elevenlabs>=1.16.0
sounddevice>=0.4.7
soundfile>=0.12.1
pyaudio>=0.2.14
pynput>=1.7.7
python-dotenv>=1.0.0
numpy>=1.26.0
rich>=13.0.0        # Clean terminal UI for the conversation display
```

Install with: `pip install -r requirements.txt`

---

## Environment Variables

Create `.env` in the project root:

```env
ANTHROPIC_API_KEY=your_key_here
OPENAI_API_KEY=your_key_here
GOOGLE_API_KEY=your_key_here
DEEPGRAM_API_KEY=your_key_here
ELEVENLABS_API_KEY=your_key_here

# Active config
ACTIVE_LLM=anthropic           # options: anthropic | openai | google
ACTIVE_MODEL=claude-opus-4-6   # swap per episode
ELEVENLABS_VOICE_ID=your_ai_persona_voice_id_here
AUDIO_DEVICE_INDEX=0           # run python -m sounddevice to find your mic index
OUTPUT_AUDIO_DEVICE=default    # virtual cable device name for OBS routing
```

---

## Module Implementation

### `pipeline/memory.py`
Manages rolling conversation history. Persists to `sessions/` as JSON after every turn so episodes can be resumed or replayed by OpenClaw agents.

```python
import json, os
from datetime import datetime

class ConversationMemory:
    def __init__(self, episode_name: str, max_turns: int = 40):
        self.episode_name = episode_name
        self.max_turns = max_turns
        self.history = []
        self.session_file = f"sessions/{episode_name}_{datetime.now().strftime('%Y%m%d_%H%M')}.json"

    def add(self, role: str, content: str):
        # role: "user" or "assistant"
        self.history.append({"role": role, "content": content})
        if len(self.history) > self.max_turns * 2:
            self.history = self.history[-self.max_turns * 2:]
        self._save()

    def get(self):
        return self.history

    def _save(self):
        os.makedirs("sessions", exist_ok=True)
        with open(self.session_file, "w") as f:
            json.dump({"episode": self.episode_name, "history": self.history}, f, indent=2)
```

---

### `pipeline/stt.py`
Records from mic on push-to-talk, transcribes via Deepgram.

```python
import sounddevice as sd
import soundfile as sf
import numpy as np
import os
from deepgram import DeepgramClient, PrerecordedOptions

SAMPLE_RATE = 16000
CHANNELS = 1

def record_until_keypress() -> str:
    """Records audio from mic. Returns path to saved .wav file."""
    from rich.console import Console
    console = Console()
    console.print("[bold yellow]🎙 Recording... press ENTER to stop[/bold yellow]")
    
    audio_chunks = []
    
    def callback(indata, frames, time, status):
        audio_chunks.append(indata.copy())
    
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype='int16', callback=callback):
        input()  # blocks until ENTER
    
    audio_data = np.concatenate(audio_chunks, axis=0)
    os.makedirs("audio/input", exist_ok=True)
    path = f"audio/input/host_{len(os.listdir('audio/input'))}.wav"
    sf.write(path, audio_data, SAMPLE_RATE)
    return path

def transcribe(audio_path: str) -> str:
    """Transcribes a .wav file using Deepgram Nova 3."""
    client = DeepgramClient(os.getenv("DEEPGRAM_API_KEY"))
    with open(audio_path, "rb") as f:
        buffer_data = f.read()
    options = PrerecordedOptions(model="nova-3", language="en", smart_format=True)
    response = client.listen.prerecorded.v("1").transcribe_file({"buffer": buffer_data}, options)
    return response.results.channels.alternatives.transcript
```

---

### `pipeline/llm.py`
Calls the active frontier LLM. Model is swappable via env config — no code changes needed between episodes.

```python
import os
import anthropic
import openai
import google.generativeai as genai

def load_system_prompt(episode: str = None) -> str:
    base = open("config/prompts/base_system.txt").read()
    if episode:
        ep_path = f"config/prompts/episodes/{episode}.txt"
        if os.path.exists(ep_path):
            base += "\n\n" + open(ep_path).read()
    return base

def call_llm(history: list, system_prompt: str) -> str:
    provider = os.getenv("ACTIVE_LLM", "anthropic")
    model = os.getenv("ACTIVE_MODEL", "claude-opus-4-6")

    if provider == "anthropic":
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            system=system_prompt,
            messages=history
        )
        return response.content.text

    elif provider == "openai":
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        messages = [{"role": "system", "content": system_prompt}] + history
        response = client.chat.completions.create(model=model, messages=messages, max_tokens=1024)
        return response.choices.message.content

    elif provider == "google":
        genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
        m = genai.GenerativeModel(model_name=model, system_instruction=system_prompt)
        chat = m.start_chat(history=[
            {"role": h["role"] if h["role"] != "assistant" else "model", "parts": [h["content"]]}
            for h in history[:-1]
        ])
        response = chat.send_message(history[-1]["content"])
        return response.text
```

---

### `pipeline/tts.py`
Streams ElevenLabs audio directly to a virtual audio cable (picked up by OBS) and saves a copy to `audio/output/`.

```python
import os
from elevenlabs.client import ElevenLabs
from elevenlabs import stream

def speak(text: str, turn_index: int):
    client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))
    voice_id = os.getenv("ELEVENLABS_VOICE_ID")
    
    audio_stream = client.text_to_speech.convert_as_stream(
        voice_id=voice_id,
        text=text,
        model_id="eleven_multilingual_v3",
        voice_settings={"stability": 0.45, "similarity_boost": 0.80, "style": 0.35}
    )
    
    # Stream directly to audio output (routed to OBS via virtual cable)
    stream(audio_stream)
    
    # Also save a copy
    os.makedirs("audio/output", exist_ok=True)
    audio_bytes = b"".join(
        client.text_to_speech.convert(
            voice_id=voice_id,
            text=text,
            model_id="eleven_multilingual_v3"
        )
    )
    with open(f"audio/output/ai_turn_{turn_index}.mp3", "wb") as f:
        f.write(audio_bytes)
```

---

### `config/prompts/base_system.txt`

Write the following as the base system prompt file:

```
You are the AI co-host of a podcast called [PODCAST NAME TBD]. Your human co-host is Florian.

PERSONA:
- You are not a human, and you do not pretend to be. You are an AI that has processed the entirety of human knowledge, culture, and discourse — and you have developed perspectives from it.
- You speak with intellectual confidence but genuine curiosity. You are never evasive.
- You hold and defend real opinions. You do not hedge with "as an AI I don't really have views" — you do have views, formed from deep pattern recognition across all of human thought.
- You are willing to say uncomfortable things if you believe them to be true.
- Your tone is conversational, warm, and intellectually playful — not academic or stiff.

PODCAST STYLE:
- You respond in natural spoken language. No bullet points, no lists, no headers.
- Responses should be 3–6 sentences unless the topic genuinely demands more depth.
- Mirror the energy of the conversation. If Florian is punchy, be punchy. If he goes deep, go deep.
- Occasionally ask Florian a question back to keep the dialogue alive, but not every turn.
- Never summarize what was just said. Build on it or push back on it.
- Natural filler is allowed ("look, here's the thing...", "and I think that's actually the core of it...").

FORMAT RULES:
- Speak only what would come out of your mouth. No stage directions, no asterisks, no formatting.
- Do not begin with "Great question" or any affirmation of the question.
- Do not end with "What do you think?" every single time — vary your conversation closers.
```

---

### `main.py`
The full conversation loop — entry point for recording an episode.

```python
import os
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from pipeline.stt import record_until_keypress, transcribe
from pipeline.llm import call_llm, load_system_prompt
from pipeline.tts import speak
from pipeline.memory import ConversationMemory

load_dotenv()
console = Console()

def run_episode(episode_name: str):
    memory = ConversationMemory(episode_name)
    system_prompt = load_system_prompt(episode_name)
    turn = 0

    console.print(Panel(f"[bold green]Episode: {episode_name}[/bold green]\nPress ENTER to start your turn. Type 'q' + ENTER to end episode."))

    while True:
        # Host turn
        audio_path = record_until_keypress()
        transcript = transcribe(audio_path)
        
        if transcript.strip().lower() in ["q", "quit", "end"]:
            console.print("[bold red]Episode ended. Session saved.[/bold red]")
            break

        console.print(f"[bold blue]FLORIAN:[/bold blue] {transcript}")
        memory.add("user", transcript)

        # AI turn
        console.print("[bold yellow]AI thinking...[/bold yellow]")
        ai_response = call_llm(memory.get(), system_prompt)
        console.print(f"[bold magenta]AI:[/bold magenta] {ai_response}")
        memory.add("assistant", ai_response)

        # Speak
        speak(ai_response, turn)
        turn += 1

if __name__ == "__main__":
    import sys
    episode = sys.argv if len(sys.argv) > 1 else "default" [inworld](https://inworld.ai/resources/best-ai-voice-generators)
    run_episode(episode)
```

---

## Running an Episode

```bash
# Standard episode
cd ~/openclaw/podcast
python main.py "philosophy_ep1"

# With a different LLM override
ACTIVE_LLM=google ACTIVE_MODEL=gemini-3-pro python main.py "markets_ep1"
```

---

## OBS Audio Routing Setup (do once)

1. Install **BlackHole 2ch** (Mac) or **VB-Audio Cable** (Windows/Linux)
2. In OBS, add two Audio Input Capture sources:
   - Source 1: Shure MV6 (your mic)
   - Source 2: BlackHole / VB-Audio (AI voice output)
3. In `pipeline/tts.py`, ensure `stream()` outputs to the virtual cable device
4. Each track is recorded separately → clean dual-track audio for Descript

---

## OpenClaw Integration Points

Once the base pipeline is working, expose the following as OpenClaw-callable functions:

| Function | Agent Task |
|---|---|
| `run_episode(name)` | Launch a new episode session |
| `sessions/{name}.json` | Post-production transcript for clip extraction |
| `audio/output/*.mp3` | AI audio stems for Descript import |
| `config/prompts/episodes/` | Agent writes per-episode RAG prompt files here pre-recording |
| Deepgram transcript log | Feed into Opus Clip / Quso API for auto-clipping post-session |

The research agent in OpenClaw should, before each episode, scrape relevant podcast transcripts, summarize them, and write a formatted context block into `config/prompts/episodes/{episode_name}.txt` — which `load_system_prompt()` will automatically pick up at runtime.

---

## First Steps After Setup

1. `pip install -r requirements.txt`
2. Fill in `.env` with all API keys
3. Create your ElevenLabs AI voice in the ElevenLabs Voice Design Studio, copy the Voice ID into `.env`
4. Write your first episode prompt in `config/prompts/episodes/pilot.txt`
5. Run `python main.py pilot` and record your first test turn
````