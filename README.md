# Multi Video Joiner

A simple desktop tool that lets you paste multiple supported video URLs, download them, and automatically join them into a single video.

I built this mainly to make my own workflow easier. Instead of downloading videos one by one and importing them into editing software just to join them, I can paste the links, arrange their order, and let the tool handle the rest.

## Features

- Paste multiple video URLs
- No fixed limit on the number of URLs
- Supports URLs handled by `yt-dlp`
- Reorder videos using drag and drop
- Remove individual URLs
- Clear all URLs
- Choose output folder
- Choose maximum quality
- Downloads videos automatically
- Normalizes different video formats using FFmpeg
- Handles differences in resolution, FPS, codecs, and audio
- Joins all videos in the selected order
- Progress and status updates
- Everything runs locally on your computer
- No subscription or server required

## How It Works

```text
Multiple URLs
      ↓
   yt-dlp
      ↓
Download videos
      ↓
    FFmpeg
      ↓
Normalize videos
(resolution / FPS / codec / audio)
      ↓
Join videos
      ↓
joined_video.mp4

Tech Stack
Python
PySide6
yt-dlp
FFmpeg
Installation
1. Clone the repository
git clone YOUR_GITHUB_REPOSITORY_URL
2. Go into the project folder
cd MULTI_VIDEO_JOINER
3. Install Python requirements
pip install -r requirements.txt
4. Install FFmpeg

FFmpeg must be installed separately and added to your system PATH.

After installation, verify it:

ffmpeg -version

You should also verify yt-dlp:

python -m yt_dlp --version
Run the Application
python main.py
Usage
Paste a supported video URL.
Click Add URL.
Add as many URLs as you need.
Drag and drop the videos to change their order.
Select the maximum quality.
Choose an output folder.
Click DOWNLOAD & JOIN.
The tool downloads, normalizes, and joins the videos automatically.
Project Structure
MULTI_VIDEO_JOINER/
│
├── main.py
├── requirements.txt
│
├── downloads/
├── temp/
└── output/
Requirements
Python 3
PySide6
yt-dlp
FFmpeg
Important Note

This tool is intended for downloading and processing content that you are authorized to download and use. It does not attempt to bypass DRM, private accounts, paywalls, or other access controls.