import os
import re
import json
import subprocess
import cv2
import numpy as np
from moviepy import VideoFileClip

# =============================
# CONFIG
# =============================

OUTPUT_DIR = "output_clips"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# =============================
# EXTRACT VIDEO ID
# =============================

def extract_video_id(url):
    match = re.search(r"(?:v=|youtu\.be/)([a-zA-Z0-9_-]+)", url)
    return match.group(1) if match else "unknown"

# =============================
# DOWNLOAD VIDEO
# =============================

def download_video(url):
    video_id = extract_video_id(url)
    output_file = f"video_{video_id}.mp4"

    if os.path.exists(output_file):
        print(f"✅ Using existing video: {output_file}")
        return output_file

    command = [
        "yt-dlp",
        "-f", "bv*[height<=1080]+ba/b[height<=1080]",
        "--merge-output-format", "mp4",
        "-o", f"video_{video_id}.%(ext)s",
        url
    ]

    subprocess.run(command)

    return output_file

# =============================
# MAKE VERTICAL
# =============================

def make_vertical(clip):
    w, h = clip.size

    target_ratio = 9 / 16
    current_ratio = w / h

    if current_ratio > target_ratio:
        new_width = int(h * target_ratio)
        x_center = w // 2
        clip = clip.cropped(x_center - new_width//2, 0, x_center + new_width//2, h)
    else:
        new_height = int(w / target_ratio)
        y_center = h // 2
        clip = clip.cropped(0, y_center - new_height//2, w, y_center + new_height//2)

    return clip.resized((1080, 1920))

# =============================
# OPENCV CAPTIONS
# =============================

def add_caption(clip, text, y_pos, color=(0,255,255), font_scale=2.0):
    def draw(frame):
        img = frame.copy()

        font = cv2.FONT_HERSHEY_SIMPLEX
        thickness = 4

        # word wrap
        words = text.split()
        lines = []
        current = ""

        for word in words:
            if len(current + " " + word) < 25:
                current += " " + word
            else:
                lines.append(current.strip())
                current = word
        lines.append(current.strip())

        y = y_pos

        for line in lines:
            text_size = cv2.getTextSize(line, font, font_scale, thickness)[0]
            x = int((img.shape[1] - text_size[0]) / 2)

            # outline
            cv2.putText(img, line, (x, y), font, font_scale, (0,0,0), thickness+3, cv2.LINE_AA)

            # main text
            cv2.putText(img, line, (x, y), font, font_scale, color, thickness, cv2.LINE_AA)

            y += 80

        return img

    return clip.image_transform(draw)

# =============================
# PROCESS CLIPS
# =============================

def process_clips(json_path, video_path):
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    clips_info = data["clip_recommendations"]
    highlights = data["highlights"]

    video = VideoFileClip(video_path)

    metadata_lines = []

    for i, clip_data in enumerate(clips_info):

        start = int(clip_data["clip_start"])
        end = int(clip_data["clip_end"])

        clip = video.subclipped(start, end)
        clip = make_vertical(clip)

        # ADD CAPTIONS
        hook = clip_data["hook_text"]["primary"]
        secondary = clip_data["hook_text"]["secondary"]

        clip = add_caption(clip, hook, 1100, (0,255,255), 2.5)
        clip = add_caption(clip, secondary, 1250, (255,255,255), 1.8)

        output_path = os.path.join(OUTPUT_DIR, f"clip_{i}.mp4")

        clip.write_videofile(
            output_path,
            fps=30,
            codec="libx264",
            audio_codec="aac"
        )
        clip.close()

        # =============================
        # METADATA
        # =============================

        title = highlights[i]["title"]
        description = highlights[i]["why_it_works"]

        hashtags = "#shorts #viral #fyp #gaming"

        metadata_lines.append(
            f"""Title: {title}
Description: {description}
Hashtags: {hashtags}
File: {output_path}
------------------------"""
        )

    with open("metadata.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(metadata_lines))

    video.close()

# =============================
# MAIN
# =============================

def main():
    video_url = input("Enter YouTube URL: ").strip()

    video_id = extract_video_id(video_url)
    json_file = f"video_{video_id}.json"

    if not os.path.exists(json_file):
        print(f"❌ Transcription not found: {json_file}")
        print("Run gemini_clip_extractor.py first to generate the transcription.")
        return

    print(f"✅ Using transcription: {json_file}")
    video_path = download_video(video_url)
    process_clips(json_file, video_path)

    print("✅ All clips generated successfully!")

if __name__ == "__main__":
    main()