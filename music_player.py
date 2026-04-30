#!/usr/bin/env python3
"""
yt-tui  —  YouTube Music TUI Player
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
pip install textual yt-dlp
ffplay must be in PATH  (ships with ffmpeg: https://ffmpeg.org/download.html)

Usage:
  python yt_tui.py                          # prompts for URL inside the TUI
  python yt_tui.py <youtube-url-or-playlist>
"""

from __future__ import annotations

import asyncio
import random
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional

import yt_dlp
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import (
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
)


# ──────────────────────────────────────────────────────────────────────────────
#  Data model
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class Track:
    url: str
    title: str = "…"
    duration: int = 0          # seconds; 0 = unknown
    stream_url: str = ""
    fetched: bool = False
    error: bool = False


# ──────────────────────────────────────────────────────────────────────────────
#  yt-dlp helpers  (all blocking — called inside @work(thread=True))
# ──────────────────────────────────────────────────────────────────────────────


def fetch_playlist_urls(url: str) -> list[str]:
    opts = {"quiet": True, "extract_flat": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if "entries" not in info:
        return [url]
    return [
        f"https://www.youtube.com/watch?v={e['id']}"
        for e in (info.get("entries") or [])
        if e
    ]


def fetch_track_info(url: str) -> tuple[str, str, int]:
    """Returns (stream_url, title, duration_seconds)."""
    opts = {"quiet": True, "format": "bestaudio"}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return (
        info["url"],
        info.get("title", "Unknown"),
        int(info.get("duration") or 0),
    )


# ──────────────────────────────────────────────────────────────────────────────
#  Audio engine
# ──────────────────────────────────────────────────────────────────────────────


class AudioEngine:
    """
    Wraps ffplay.  Because Windows has no SIGSTOP, pause = kill + store position.
    Resume = restart ffplay with  -ss <position>.
    A background thread increments the position counter every 250 ms.
    """

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._pos: float = 0.0
        self._paused: bool = False
        self._timer_active: bool = False
        self._timer_gen: int = 0
        self._stream_url: str = ""

    # ── public ────────────────────────────────────────────────────

    def play(self, stream_url: str, seek: float = 0.0) -> None:
        self._stop_proc()
        self._stream_url = stream_url
        self._pos = seek
        self._paused = False
        self._launch(seek)

    def toggle_pause(self) -> None:
        if self._paused:
            self._paused = False
            self._launch(self._pos)
        else:
            self._stop_proc()
            self._paused = True

    def stop(self) -> None:
        self._timer_active = False
        self._stop_proc()
        self._pos = 0.0
        self._paused = False

    def seek_relative(self, delta: float) -> None:
        """Jump forward/backward by delta seconds (approximate via restart)."""
        new_pos = max(0.0, self._pos + delta)
        self._stop_proc()
        self._pos = new_pos
        if not self._paused:
            self._launch(new_pos)

    @property
    def position(self) -> float:
        return self._pos

    @property
    def paused(self) -> bool:
        return self._paused

    def finished(self) -> bool:
        """True when ffplay exited naturally (track over)."""
        with self._lock:
            return (
                self._proc is not None
                and not self._paused
                and self._proc.poll() is not None
                and self._proc.returncode == 0
            )

    # ── private ───────────────────────────────────────────────────

    def _launch(self, seek: float) -> None:
        cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]
        if seek > 1:
            cmd += ["-ss", str(int(seek))]
        cmd.append(self._stream_url)
        with self._lock:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        self._timer_active = True
        gen = self._timer_gen
        threading.Thread(target=self._timer, args=(gen,), daemon=True).start()

    def _stop_proc(self) -> None:
        self._timer_active = False
        self._timer_gen += 1  # invalidate any running timer threads
        with self._lock:
            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.terminate()
                except Exception:
                    pass
            self._proc = None

    def _timer(self, gen: int) -> None:
        while self._timer_active and self._timer_gen == gen:
            time.sleep(0.25)
            if self._timer_active and not self._paused and self._timer_gen == gen:
                self._pos += 0.25


# ──────────────────────────────────────────────────────────────────────────────
#  Helper
# ──────────────────────────────────────────────────────────────────────────────


def fmt_time(secs: float) -> str:
    s = max(0, int(secs))
    return f"{s // 60:02d}:{s % 60:02d}"


# ──────────────────────────────────────────────────────────────────────────────
#  Widgets
# ──────────────────────────────────────────────────────────────────────────────


class NowPlaying(Static):
    """Renders the now-playing panel: album-art placeholder, title, progress."""

    title_rx: reactive[str] = reactive("No track loaded")
    position: reactive[float] = reactive(0.0)
    duration: reactive[int] = reactive(0)
    paused: reactive[bool] = reactive(False)
    status_msg: reactive[str] = reactive("")      # transient messages (e.g. "Shuffled!")
    track_num: reactive[str] = reactive("")

    def render(self) -> Text:
        t = Text()

        # ── track number + title ──────────────────────────────────
        num = f" {self.track_num}  " if self.track_num else "  "
        title = self.title_rx
        if len(title) > 60:
            title = title[:58] + "…"
        t.append(num, style="bold #5c6370")
        t.append(title + "\n", style="bold #61afef")

        # ── progress bar ─────────────────────────────────────────
        BAR = 52
        elapsed = min(self.position, self.duration or self.position)
        total = self.duration or max(elapsed, 1)
        filled = int(BAR * elapsed / total)
        filled = max(0, min(BAR, filled))
        bar_filled = "━" * filled
        bar_empty = "╌" * (BAR - filled)
        pos_s = fmt_time(elapsed)
        dur_s = fmt_time(self.duration) if self.duration else "--:--"

        t.append(f"\n  {pos_s} ", style="#5c6370")
        t.append(bar_filled, style="bold #98c379")
        t.append("●", style="bold #e5c07b") if filled < BAR else t.append("", style="")
        t.append(bar_empty, style="#3b4048")
        t.append(f" {dur_s}\n", style="#5c6370")

        # ── status line ──────────────────────────────────────────
        if self.status_msg:
            t.append(f"\n  {self.status_msg}", style="bold #e5c07b")
        elif self.paused:
            t.append("\n  ⏸  Paused", style="bold #e5c07b")
        else:
            t.append("\n  ▶  Playing", style="bold #98c379")

        return t


class TrackItem(Static):
    """A single row in the queue list."""

    is_current: reactive[bool] = reactive(False)

    def __init__(self, index: int, track: Track, **kw):
        super().__init__(**kw)
        self.index = index
        self.track = track

    def render(self) -> Text:
        t = Text()
        num = f"{self.index + 1:>3}."
        title = self.track.title
        if len(title) > 54:
            title = title[:52] + "…"
        dur = fmt_time(self.track.duration) if self.track.duration else "  ···"

        if self.is_current:
            t.append(f"  ▶ {num}  ", style="bold #98c379")
            t.append(f"{title:<54}", style="bold white")
            t.append(f"  {dur}", style="#98c379")
        elif self.track.error:
            t.append(f"  ✗ {num}  ", style="#e06c75")
            t.append(f"{title:<54}", style="#5c6370")
            t.append(f"  {dur}", style="#5c6370")
        else:
            t.append(f"    {num}  ", style="#5c6370")
            t.append(f"{title:<54}", style="#abb2bf")
            t.append(f"  {dur}", style="#5c6370")
        return t


# ──────────────────────────────────────────────────────────────────────────────
#  App
# ──────────────────────────────────────────────────────────────────────────────


APP_CSS = """
Screen {
    background: #282c34;
    color: #abb2bf;
    layers: base overlay;
}

/* ── top bar ── */
#top-bar {
    height: 1;
    background: #21252b;
    padding: 0 2;
    dock: top;
}
#app-name {
    color: #61afef;
    text-style: bold;
    width: 1fr;
}
#track-counter {
    color: #5c6370;
    text-align: right;
    width: auto;
}

/* ── now playing ── */
#now-playing-outer {
    height: 9;
    border: solid #3b4048;
    background: #21252b;
    margin: 1 1 0 1;
    padding: 1 2;
}

/* ── URL input (shown only when no URL given) ── */
#url-bar {
    height: 3;
    margin: 0 1;
    display: none;
}
#url-bar.visible {
    display: block;
}
#url-input {
    border: solid #3b4048;
    background: #21252b;
    color: #abb2bf;
}
#url-input:focus {
    border: solid #61afef;
}

/* ── queue ── */
#queue-header {
    height: 1;
    padding: 0 2;
    margin-top: 1;
    color: #5c6370;
    background: #282c34;
}
ListView {
    border: solid #3b4048;
    background: #282c34;
    margin: 0 1 0 1;
    height: 1fr;
    scrollbar-color: #3b4048;
    scrollbar-color-hover: #61afef;
}
ListItem {
    padding: 0;
    height: 1;
    background: #282c34;
}
ListItem:hover {
    background: #2c313a;
}
ListItem.--highlight {
    background: #2c313a;
    border-left: solid #61afef;
}

/* ── help bar ── */
#help {
    height: 1;
    padding: 0 1;
    background: #21252b;
    color: #5c6370;
    dock: bottom;
    margin-bottom: 2;   /* leave room for Footer */
}

Footer {
    background: #21252b;
    color: #5c6370;
}
Footer > .footer--key {
    background: #3b4048;
    color: #61afef;
}
"""


class MusicApp(App):
    CSS = APP_CSS

    BINDINGS = [
        Binding("space",     "toggle_pause",  "Play/Pause"),
        Binding("n",         "next_track",    "Next"),
        Binding("p",         "prev_track",    "Prev"),
        Binding("s",         "shuffle",       "Shuffle"),
        Binding("right",     "seek_fwd",      "→ 10s",   show=False),
        Binding("left",      "seek_back",     "← 10s",   show=False),
        Binding("r",         "restart",       "Restart", show=False),
        Binding("ctrl+l",    "focus_input",   "Load URL",show=False),
        Binding("q",         "quit",          "Quit"),
    ]

    current_index: reactive[int] = reactive(-1)

    def __init__(self, playlist_url: str = "") -> None:
        super().__init__()
        self.playlist_url = playlist_url
        self.tracks: list[Track] = []
        self.engine = AudioEngine()
        self._status_clear_handle = None
        self._shuffle_original: list[Track] = []

    # ── layout ────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        with Horizontal(id="top-bar"):
            yield Label("♫  yt-tui", id="app-name")
            yield Label("", id="track-counter")

        yield NowPlaying(id="now-playing-outer")

        with Container(id="url-bar"):
            yield Input(
                placeholder="Paste a YouTube URL or playlist link, then press Enter …",
                id="url-input",
            )

        yield Label(
            " QUEUE                                                     "
            "                                            [dim]↑↓ scroll · Enter play[/dim]",
            id="queue-header",
            markup=True,
        )
        yield ListView(id="queue")

        yield Static(
            " [b]Spc[/b] pause  [b]N[/b] next  [b]P[/b] prev  "
            "[b]S[/b] shuffle  [b]←/→[/b] seek 10s  [b]R[/b] restart  "
            "[b]Ctrl+L[/b] load URL  [b]Q[/b] quit",
            id="help",
            markup=True,
        )
        yield Footer()

    def on_mount(self) -> None:
        self.title = "yt-tui"
        self.set_interval(0.4, self._tick)

        if self.playlist_url:
            self._load_playlist(self.playlist_url)
        else:
            # Show the URL input bar
            self.query_one("#url-bar").add_class("visible")
            self.query_one("#url-input", Input).focus()

    # ── URL input ─────────────────────────────────────────────────

    @on(Input.Submitted, "#url-input")
    def url_submitted(self, event: Input.Submitted) -> None:
        url = event.value.strip()
        if not url:
            return
        self.query_one("#url-bar").remove_class("visible")
        self._load_playlist(url)

    def action_focus_input(self) -> None:
        self.query_one("#url-bar").add_class("visible")
        self.query_one("#url-input", Input).focus()

    # ── loading ───────────────────────────────────────────────────

    @work(thread=True)
    def _load_playlist(self, url: str) -> None:
        now = self.query_one(NowPlaying)
        self.call_from_thread(setattr, now, "title_rx", "Fetching playlist…")
        self.call_from_thread(setattr, now, "status_msg", "⏳  Connecting to YouTube…")
        try:
            urls = fetch_playlist_urls(url)
        except Exception as exc:
            self.call_from_thread(
                setattr, now, "title_rx", f"Error: {exc}"
            )
            self.call_from_thread(setattr, now, "status_msg", "")
            return

        self.tracks = [Track(u) for u in urls]
        self._shuffle_original = list(self.tracks)
        self.call_from_thread(self._rebuild_list)
        self.call_from_thread(self._update_counter)

        # Prefetch metadata for first 5 tracks
        for i in range(min(5, len(self.tracks))):
            self._prefetch(i)

        # Auto-play first track
        self.call_from_thread(self._play_index, 0)

    @work(thread=True)
    def _prefetch(self, index: int) -> None:
        if index >= len(self.tracks):
            return
        track = self.tracks[index]
        if track.fetched:
            return
        try:
            stream_url, title, duration = fetch_track_info(track.url)
            track.stream_url = stream_url
            track.title = title
            track.duration = duration
            track.fetched = True
        except Exception:
            track.title = "[unavailable]"
            track.error = True
            track.fetched = True
        self.call_from_thread(self._refresh_row, index)

    # ── playback ──────────────────────────────────────────────────

    def _play_index(self, index: int) -> None:
        if not self.tracks or not (0 <= index < len(self.tracks)):
            return

        self.current_index = index
        track = self.tracks[index]
        now = self.query_one(NowPlaying)

        # Scroll list to current track
        lv = self.query_one("#queue", ListView)
        lv.index = index

        # Highlight rows
        for i, item in enumerate(lv.query(ListItem)):
            rows = item.query(TrackItem)
            if rows:
                rows.first().is_current = i == index
                rows.first().refresh()

        self._update_counter()

        if not track.fetched:
            now.title_rx = "Loading…"
            now.status_msg = "⏳  Fetching audio stream…"
            now.paused = False
            self._play_after_fetch(index)
            return

        if track.error:
            self._set_status("⚠  Skipping unavailable track", auto_clear=True)
            self.call_later(self.action_next_track)
            return

        self.engine.play(track.stream_url)
        now.title_rx = track.title
        now.duration = track.duration
        now.position = 0.0
        now.paused = False
        now.status_msg = ""
        now.track_num = f"[{index + 1}/{len(self.tracks)}]"

        # Pre-fetch upcoming tracks
        for i in range(index + 1, min(index + 6, len(self.tracks))):
            self._prefetch(i)

    @work(thread=True)
    def _play_after_fetch(self, index: int) -> None:
        track = self.tracks[index]
        if not track.fetched:
            try:
                stream_url, title, duration = fetch_track_info(track.url)
                track.stream_url = stream_url
                track.title = title
                track.duration = duration
                track.fetched = True
            except Exception:
                track.title = "[unavailable]"
                track.error = True
                track.fetched = True
        self.call_from_thread(self._play_index, index)

    # ── tick ─────────────────────────────────────────────────────

    def _tick(self) -> None:
        if not self.tracks or self.current_index < 0:
            return
        now = self.query_one(NowPlaying)
        now.position = self.engine.position
        now.paused = self.engine.paused

        # Auto-advance when track finishes
        if self.engine.finished():
            next_i = (self.current_index + 1) % len(self.tracks)
            if next_i == 0 and self.current_index == len(self.tracks) - 1:
                self._set_status("⏹  Playlist complete")
                self.engine.stop()
            else:
                self._play_index(next_i)

    # ── list helpers ──────────────────────────────────────────────

    def _rebuild_list(self) -> None:
        lv = self.query_one("#queue", ListView)
        lv.clear()
        for i, t in enumerate(self.tracks):
            row = TrackItem(i, t)
            lv.append(ListItem(row))

    def _refresh_row(self, index: int) -> None:
        lv = self.query_one("#queue", ListView)
        items = list(lv.query(ListItem))
        if index < len(items):
            item = items[index]
            rows = item.query(TrackItem)
            if rows:
                rows.first().track = self.tracks[index]
                rows.first().refresh()

    def _update_counter(self) -> None:
        if not self.tracks:
            return
        n = len(self.tracks)
        cur = self.current_index + 1 if self.current_index >= 0 else 0
        self.query_one("#track-counter", Label).update(f"{cur} / {n} tracks")

    def _set_status(self, msg: str, auto_clear: bool = False) -> None:
        now = self.query_one(NowPlaying)
        now.status_msg = msg
        if auto_clear:
            def _clear():
                now.status_msg = ""
            self.set_timer(2.5, _clear)

    # ── actions ───────────────────────────────────────────────────

    def action_toggle_pause(self) -> None:
        if not self.tracks or self.current_index < 0:
            return
        self.engine.toggle_pause()

    def action_next_track(self) -> None:
        if not self.tracks:
            return
        nxt = (self.current_index + 1) % len(self.tracks)
        self._play_index(nxt)

    def action_prev_track(self) -> None:
        if not self.tracks:
            return
        # If more than 3 s elapsed → restart; else go to previous
        if self.engine.position > 3.0 and not self.engine.paused:
            track = self.tracks[self.current_index]
            if track.fetched and not track.error:
                self.engine.play(track.stream_url, seek=0.0)
                self.query_one(NowPlaying).position = 0.0
        else:
            prev = (self.current_index - 1) % len(self.tracks)
            self._play_index(prev)

    def action_shuffle(self) -> None:
        if not self.tracks:
            return
        random.shuffle(self.tracks)
        self.current_index = -1
        self._rebuild_list()
        self.call_later(self._play_index, 0)
        self._set_status("🔀  Queue shuffled!", auto_clear=True)

    def action_seek_fwd(self) -> None:
        self.engine.seek_relative(10.0)

    def action_seek_back(self) -> None:
        self.engine.seek_relative(-10.0)

    def action_restart(self) -> None:
        if self.current_index < 0:
            return
        track = self.tracks[self.current_index]
        if track.fetched and not track.error:
            self.engine.play(track.stream_url, seek=0.0)
            self.query_one(NowPlaying).position = 0.0

    # ── list selection ────────────────────────────────────────────

    @on(ListView.Selected)
    def list_selected(self, event: ListView.Selected) -> None:
        items = list(self.query_one("#queue", ListView).query(ListItem))
        try:
            idx = items.index(event.item)
        except ValueError:
            return
        self._play_index(idx)

    # ── cleanup ───────────────────────────────────────────────────

    def on_unmount(self) -> None:
        self.engine.stop()


# ──────────────────────────────────────────────────────────────────────────────
#  Entry point
# ──────────────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else ""
    MusicApp(playlist_url=url).run()