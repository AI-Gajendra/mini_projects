# Mini Projects

A collection of single-file Python utilities built to solve day-to-day problems — each one created with the help of AI models (Claude, GPT, Gemini).

> Every project here is **one Python file**. No complex setup, no bloated dependencies — just drop the file and run it.

---

## Projects

| # | File | Description |
|---|------|-------------|
| 1 | [music_player.py](#1-music_playerpy) | Terminal-based YouTube music player (TUI) |

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
