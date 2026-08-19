from pathlib import Path
import yt_dlp


DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)


def download_video(url):
    options = {
        "format": "bv*[height<=1080]+ba/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(DOWNLOAD_DIR / "%(title)s.%(ext)s"),
        "noplaylist": True,
    }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])

        print("\n✅ Download completed!")

    except Exception as error:
        print(f"\n❌ Download failed:\n{error}")


url = input("Paste a video URL: ").strip()

if url:
    download_video(url)
else:
    print("❌ No URL entered.")