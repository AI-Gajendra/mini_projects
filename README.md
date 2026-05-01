# Mini Projects

A collection of single-file Python utilities built to solve day-to-day problems — each one created with the help of AI models (Claude, GPT, Gemini).

> Every project here is **one Python file**. No complex setup, no bloated dependencies — just drop the file and run it.

---

## Projects

| # | File | Description |
|---|------|-------------|
| 1 | [music_player.py](#1-music_playerpy) | Terminal-based YouTube music player (TUI) |
| 2 | [gemini_clip_extractor.py](#2-gemini_clip_extractorpy) | AI-powered viral clip detector for YouTube videos using Gemini |

---

## 1. `music_player.py`

A terminal UI music player that streams audio directly from YouTube — supports single tracks, playlists, shuffle, and seek. Built with `textual`, `yt-dlp`, and `ffplay`.

### Requirements

| Dependency | Install |
|------------|---------|
| Python 3.8+ | — |
| `textual` | `pip install textual` |
| `yt-dlp` | `pip install yt-dlp` |
| `ffplay` | Ships with [ffmpeg](https://ffmpeg.org/download.html) — must be in PATH |

Install Python dependencies in one shot:

```bash
pip install textual yt-dlp
```

Install ffmpeg (Windows — using winget):

```bash
winget install ffmpeg
```

Or download the binary directly from https://ffmpeg.org/download.html and add the `bin/` folder to your system PATH.

### Usage

**Launch without a URL** (a prompt will appear inside the TUI):

```bash
python music_player.py
```

**Launch with a YouTube video or playlist URL:**

```bash
python music_player.py https://www.youtube.com/watch?v=XXXXXXXXXXX
python music_player.py https://www.youtube.com/playlist?list=XXXXXXXXXXX
```

### Keyboard Controls

| Key | Action |
|-----|--------|
| `Space` | Play / Pause |
| `N` | Next track |
| `P` | Previous track |
| `S` | Shuffle queue |
| `→` | Seek forward 10 seconds |
| `←` | Seek backward 10 seconds |
| `R` | Restart current track |
| `Ctrl+L` | Open URL input to load a new URL |
| `Q` | Quit |

### Screenshot

```
┌─────────────────────────────────────────────────────────────────┐
│ ♫  yt-tui                                           1 / 12      │
├─────────────────────────────────────────────────────────────────┤
│  1  Lofi Hip Hop Radio - Beats to Study/Relax to               │
│                                                                 │
│  02:14 ━━━━━━━━━━━━━━━━━━━━━━●╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌ 05:32         │
│                                                                 │
│  ▶  Playing                                                     │
├─────────────────────────────────────────────────────────────────┤
│  QUEUE                                                          │
│  ▶  1.  Lofi Hip Hop Radio - Beats to Study/Relax        02:14 │
│     2.  ChillHop Essentials                              03:47 │
│     ...                                                         │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. `gemini_clip_extractor.py`

Sends a YouTube video to Gemini's multimodal API and gets back a full clip analysis — timestamped transcript, visual descriptions, viral highlight detection, kinetic subtitle specs, and clip editing notes. Output is saved as a JSON file for use in video editing pipelines.

### Requirements

| Dependency | Install |
|------------|----------|
| Python 3.8+ | — |
| `google-genai` | `pip install google-genai` |
| Gemini API key | [Get one free](https://aistudio.google.com/app/apikey) |

```bash
pip install google-genai
```

### Setup

Export your Gemini API key as an environment variable:

```bash
# Windows (PowerShell)
$env:GEMINI_API_KEY = "your_api_key_here"

# Windows (Command Prompt)
set GEMINI_API_KEY=your_api_key_here

# macOS / Linux
export GEMINI_API_KEY=your_api_key_here
```

### Usage

```bash
python gemini_clip_extractor.py
```

You will be prompted to paste a YouTube URL. The script will:

1. Send the video to Gemini with a multimodal analysis prompt
2. Receive a structured JSON response containing:
   - Full timestamped transcript (every 2–5 seconds)
   - Visual analysis aligned to transcript timestamps
   - 5–10 detected viral highlight moments with scores
   - Full clip specs with kinetic subtitle segments
   - 2 rejected clips with reasoning
3. Save the result to a file named `video_<hash>.json` in the current directory

### Output Structure

```json
{
  "content_type": "PODCAST",
  "video_energy_level": "high",
  "transcript": [...],
  "visual_analysis": [...],
  "highlights": [
    {
      "start_time": 42,
      "end_time": 87,
      "title": "He said this on camera and immediately regretted it",
      "hook_strength": 91,
      "payoff_strength": 88,
      "rewatchability": 76,
      "emotion_type": "shock"
    }
  ],
  "clip_recommendations": [...],
  "rejected_clips": [...]
}
```

### Clip Recommendation Fields

| Field | Description |
|-------|-------------|
| `clip_id` | Unique snake_case identifier |
| `clip_start` / `clip_end` | Timestamps in seconds |
| `hook_text` | Primary (ALL CAPS) and secondary hook lines |
| `kinetic_subtitle_segments` | Per-second subtitle cues with animation type |
| `platform_suitability` | e.g. `["TikTok", "YouTube Shorts", "Reels"]` |
| `editing_notes` | Cut, zoom, pace, and audio instructions |
| `loop_note` | How the ending reconnects to the opening hook |

---

## Adding a New Project

When a new single-file project is added:

1. Drop the `.py` file into the root of this repo.
2. Add a row to the [Projects](#projects) table above.
3. Add a new numbered section below the last project following the same format:
   - Brief description
   - Requirements table
   - Install commands
   - Usage examples
   - Keyboard controls or CLI flags (if any)

---

## License

MIT
