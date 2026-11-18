import os
import sys
import httpx
from PIL import Image
import io

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "tests", "fixtures", "realistic_images")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Curated real photographic images from Unsplash (free to use under Unsplash License)
SAMPLES = [
    {
        "filename": "photo1_birthday.jpg",
        "description": "Birthday celebration with party decorations and festive atmosphere",
        "url": "https://images.unsplash.com/photo-1513151233558-d860c5398176?w=640&auto=format&fit=crop&q=80"
    },
    {
        "filename": "photo2_anniversary.jpg",
        "description": "Romantic anniversary wedding rings and flowers celebration",
        "url": "https://images.unsplash.com/photo-1515934751635-c81c6bc9a2d8?w=640&auto=format&fit=crop&q=80"
    },
    {
        "filename": "photo3_mountains.jpg",
        "description": "Scenic majestic alpine mountain peaks and hiking trail",
        "url": "https://images.unsplash.com/photo-1464822759023-fed622ff2c3b?w=640&auto=format&fit=crop&q=80"
    },
    {
        "filename": "photo4_beach_sunset.jpg",
        "description": "Golden sunset over ocean waves on a sandy beach shore",
        "url": "https://images.unsplash.com/photo-1507525428034-b723cf961d3e?w=640&auto=format&fit=crop&q=80"
    },
    {
        "filename": "photo5_family_feast.jpg",
        "description": "Warm festive dinner table banquet with celebratory feast",
        "url": "https://images.unsplash.com/photo-1511795409834-ef04bbd61622?w=640&auto=format&fit=crop&q=80"
    }
]

def download_samples():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) VideoGenApp/1.0"}
    for item in SAMPLES:
        out_path = os.path.join(OUTPUT_DIR, item["filename"])
        if os.path.exists(out_path) and os.path.getsize(out_path) > 10000:
            print(f"Already exists: {item['filename']} ({os.path.getsize(out_path)} bytes)")
            continue

        print(f"Downloading {item['filename']} from {item['url']}...")
        try:
            r = httpx.get(item["url"], headers=headers, timeout=20.0, follow_redirects=True)
            if r.status_code == 200 and len(r.content) > 5000:
                # Validate it is a genuine readable image using PIL
                img = Image.open(io.BytesIO(r.content))
                img.verify()
                with open(out_path, "wb") as f:
                    f.write(r.content)
                print(f"Successfully saved {item['filename']} ({len(r.content)} bytes, format: {img.format})")
            else:
                print(f"Failed with status {r.status_code}, length: {len(r.content)}")
        except Exception as e:
            print(f"Error downloading {item['filename']}: {e}")

if __name__ == "__main__":
    download_samples()
