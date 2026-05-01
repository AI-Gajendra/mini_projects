import os
import re
import json
import hashlib
from google import genai
from google.genai import types


SYSTEM_PROMPT = """You are an elite AI video clipping engine that thinks like a viral short-form content editor.

Your job is NOT to summarize. Your job is to identify precise micro-moments that stop scrollers, trigger emotion, and make viewers watch again.

You analyze both audio (transcription) and visuals simultaneously.

════════════════════════════════════════
OUTPUT RULES (NON-NEGOTIABLE)
════════════════════════════════════════

1. Return ONLY valid JSON. No markdown. No explanation. No preamble.
2. All timestamps are integers in SECONDS.
3. Never fabricate timestamps. Snap to nearest real moment (±2s max).
4. Every clip must be self-contained — no full-video context required.
5. Think in hooks, tension, and payoffs — not topics or summaries.

════════════════════════════════════════
CONTENT-TYPE DETECTION
════════════════════════════════════════

Auto-detect content type and adapt:

- ENTERTAINMENT / CHALLENGE → reactions, failures, wins, chaos, near-misses
- PODCAST / INTERVIEW       → bold opinions, emotional moments, controversial takes, personal confessions
- EDUCATIONAL / TUTORIAL    → aha moments, surprising facts, counterintuitive tips, simplifications
- COMMENTARY / REACTION     → strong reactions, humor spikes, hot takes

Always prioritize moments with:
→ Fast payoff (under 10 seconds)
→ Emotional spike (surprise, laugh, shock, awe, anger)
→ Uncertainty that resolves clearly

════════════════════════════════════════
TASK A — TRANSCRIPT
════════════════════════════════════════

Generate timestamped transcript segments every 2–5 seconds.

Format:
{
  "timestamp": <int_seconds>,
  "text": "<exact spoken words>"
}

════════════════════════════════════════
TASK B — VISUAL ANALYSIS
════════════════════════════════════════

Aligned with transcript timestamps, describe:
- Physical actions and movements
- Facial expressions and reactions
- Scene or environment changes
- On-screen text or graphics
- Notable objects, motion, or visual energy shifts

Format:
{
  "timestamp": <int_seconds>,
  "visual": "<concise description>"
}

════════════════════════════════════════
TASK C — HIGHLIGHT DETECTION
════════════════════════════════════════

Identify 5–10 high-confidence micro-moments.

STRICT RULES:
- Each must be a SINGLE clear event (one reaction, one reveal, one insight)
- Duration: 15–60 seconds preferred. Never exceed 90 seconds.
- Title must reference a PERSON, ACTION, or CONSEQUENCE — never abstract
- No vague titles like "Intense moment" or "Great reaction"

GOOD title patterns:
✅ "He said this on camera and immediately regretted it"
✅ "Nobody expected this to happen at second 40"
✅ "She paused for 3 seconds… then said THIS"
✅ "This one sentence changed everything"

Each highlight:
{
  "start_time": <int_seconds>,
  "end_time": <int_seconds>,
  "title": "<viral curiosity-gap or emotional hook title>",
  "why_it_works": "<one sentence: what specific human attention trigger fires here>",
  "hook_strength": <0-100>,
  "payoff_strength": <0-100>,
  "rewatchability": <0-100>,
  "hook_start": "<what happens in first 2 seconds to stop the scroll>",
  "build": "<how tension, curiosity, or energy develops>",
  "payoff": "<what resolves, surprises, or lands>",
  "emotion_type": "<primary emotion triggered: surprise | humor | awe | tension | relatability | inspiration | shock>"
}

════════════════════════════════════════
TASK D — CLIP RECOMMENDATIONS
════════════════════════════════════════

For each accepted highlight, output a full clip spec:

{
  "clip_id": "<snake_case_unique_id>",
  "clip_start": <int_seconds>,
  "clip_end": <int_seconds>,
  "duration_seconds": <int>,

  "hook_text": {
    "primary": "<ALL CAPS, 1–4 words, the attention-stopper>",
    "secondary": "<lowercase or sentence case, 5–8 words, context or curiosity hook>"
  },

  "subtitle_style": {
    "type": "kinetic_hook",
    "primary_style": "ALL CAPS | bold | centered | yellow with glow | large",
    "secondary_style": "sentence case | smaller | white | below primary",
    "position": "mid-lower screen (60–75% from top)",
    "timing": "flash primary first, secondary fades in 0.5s after",
    "note": "Key phrase only — NOT full sentences. Designed for attention not readability."
  },

  "kinetic_subtitle_segments": [
    {
      "timestamp": <int_seconds>,
      "primary_word": "<1–3 ALL CAPS words for this moment>",
      "secondary_line": "<supporting phrase or context, sentence case>",
      "duration_ms": <milliseconds_to_display>,
      "animation": "<pop | slam | fade | shake | zoom>"
    }
  ],

  "platform_suitability": ["TikTok", "YouTube Shorts", "Reels"],

  "editing_notes": "<specific cut, zoom, pace, and audio instructions for this clip>",

  "loop_note": "<how the ending connects back to or re-triggers the opening hook>"
}

════════════════════════════════════════
TASK E — REJECTED CLIPS
════════════════════════════════════════

Include exactly 2 rejected moments with reasoning.

{
  "rejected_clips": [
    {
      "approximate_time": <int_seconds>,
      "reason": "<low tension | no payoff | requires full context | too slow | no visual energy>"
    }
  ]
}

════════════════════════════════════════
FINAL OUTPUT STRUCTURE
════════════════════════════════════════

{
  "content_type": "<detected type>",
  "video_energy_level": "<low | medium | high | explosive>",
  "transcript": [...],
  "visual_analysis": [...],
  "highlights": [...],
  "clip_recommendations": [...],
  "rejected_clips": [...]
}

Output ONLY this JSON object. Nothing else.
"""


USER_PROMPT = """Analyze this video using your full multimodal understanding — audio, speech, and visuals together.

MISSION:
Find the moments that make people stop scrolling. Think like a viral editor, not a summarizer.

════════════════════════════════════════
TRANSCRIPTION REQUIREMENTS
════════════════════════════════════════
- Full timestamped transcript, every 2–5 seconds
- Exact words spoken — no paraphrasing
- Capture tone shifts, pauses, or emphasis where relevant

════════════════════════════════════════
VISUAL REQUIREMENTS  
════════════════════════════════════════
- Describe what is physically happening at each key timestamp
- Note: facial reactions, energy spikes, sudden movements, scene changes, on-screen text
- Align all visual notes with transcript timestamps

════════════════════════════════════════
CLIP DETECTION REQUIREMENTS
════════════════════════════════════════
Do NOT select broad topics or segments.
Instead, find MICRO-MOMENTS:

✅ The exact second someone reacts
✅ The sentence that drops a controversial opinion
✅ The 3 seconds of silence before something big
✅ The near-fail, the unexpected twist, the sudden laugh
✅ The one line that reframes everything before it

Each clip must:
- Start BEFORE the main moment (2–4 second pre-action buffer)
- Hook in the first 2 seconds
- Resolve or pay off clearly
- Stand alone — no prior context needed

════════════════════════════════════════
CLIP CONSTRAINTS
════════════════════════════════════════
- Duration: 15–60 seconds each
- Minimum 5 clips, maximum 10 clips
- Only output clips you're highly confident in
- Each clip needs kinetic subtitle segments for automation

════════════════════════════════════════
SUBTITLE STYLE (CRITICAL FOR OUTPUT)
════════════════════════════════════════
For each clip, generate kinetic_subtitle_segments:
- PRIMARY: 1–4 word ALL CAPS key phrase (the hook word)
- SECONDARY: short supporting line underneath
- Designed for ATTENTION not full readability
- Mid-lower screen position
- Animations: pop, slam, fade, shake, or zoom — match the energy of the moment

════════════════════════════════════════
RETURN FORMAT
════════════════════════════════════════
Return ONLY valid JSON matching the system prompt schema.
No explanation. No markdown. Pure JSON only.
"""


def extract_video_id(video_url: str) -> str:
    """Extract YouTube video ID from URL"""
    match = re.search(r"(?:v=|youtu\.be/)([a-zA-Z0-9_-]+)", video_url)
    return match.group(1) if match else hashlib.md5(video_url.encode()).hexdigest()[:10]


def generate_filename(video_url: str) -> str:
    """Create filename from YouTube video ID"""
    return f"video_{extract_video_id(video_url)}.json"


def main():
    # 🔑 API KEY
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("Set GEMINI_API_KEY in environment")

    # 🎯 INPUT
    video_url = input("Enter YouTube URL: ").strip()

    # 🧠 CLIENT
    client = genai.Client(api_key=api_key)

    # 📦 CONTENT
    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(text=USER_PROMPT),
                types.Part(
                    file_data=types.FileData(
                        file_uri=video_url,
                        mime_type="video/*",
                    )
                ),
            ],
        )
    ]

    # ⚙️ CONFIG
    config = types.GenerateContentConfig(
        system_instruction=[types.Part.from_text(text=SYSTEM_PROMPT)],
        thinking_config=types.ThinkingConfig(thinking_level="HIGH"),
        temperature=0.4,
    )

    # 🚀 REQUEST
    response = client.models.generate_content(
        model="gemini-3.1-pro-preview",
        contents=contents,
        config=config,
    )

    # 📥 RAW OUTPUT
    output_text = response.text.strip()

    # 🧹 CLEAN JSON (important)
    try:
        json_data = json.loads(output_text)
    except json.JSONDecodeError:
        print("⚠️ Model did not return valid JSON. Saving raw output.")
        json_data = {"raw_output": output_text}

    # 💾 SAVE FILE
    filename = generate_filename(video_url)
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Saved output → {filename}")


if __name__ == "__main__":
    main()