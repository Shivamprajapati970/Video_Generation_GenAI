# Static Music Library

This directory hosts local royalty-free and development audio tracks for the Video Generation AI project.

## Current Audio Classification Notice

> [!WARNING]
> **Synthetic Development Audio Notice**: The current audio files (`.wav`) in this directory are programmatically synthesized harmonic audio motifs created by the in-house VideoGen AI test generator. They are provided solely for local testing, pipeline verification, and client UI demonstrations. They are **NOT** claimed as CC0 or third-party verified royalty-free masters. Before commercial deployment, replace them with verified licensed audio tracks.

## Directory Structure

```text
backend/media/music/
├── README.md               # Documentation and licensing guide
├── catalog.json            # Machine-readable music tracks metadata registry
├── anniversary/            # Romantic, warm, and milestone tracks
│   └── romantic_piano_warmth.wav
├── birthday/               # Upbeat, cheerful, and festive celebration tracks
│   └── happy_birthday_acoustic.wav
├── cinematic/              # Majestic, grand orchestral scores and sweeps
│   └── majestic_orchestral_rise.wav
├── family/                 # Heartwarming, warm acoustic folk and gathering tracks
│   └── sunny_family_afternoon.wav
├── friends/                # Breezy, lighthearted, roadtrip, and ukulele tracks
│   └── carefree_friends_ukulele.wav
└── memories/               # Contemplative, nostalgic, and reflective melodies
    └── nostalgic_acoustic_reflections.wav
```

## Adding Licensed Royalty-Free Tracks for Production

To replace or extend the library with studio-recorded licensed tracks:

1. **Licence Verification**:
   - Only add tracks licensed under **Creative Commons Zero (CC0)**, **Public Domain**, or commercial-friendly **CC-BY 4.0** (with explicit author attribution), or commercially purchased royalty-free tracks with documented licenses.
   - Do NOT download copyrighted commercial music without licensing.

2. **Save Audio File**:
   - Place `.wav` (PCM 16-bit, 44.1 kHz stereo recommended) or `.mp3` (192+ kbps) inside the corresponding category directory.
   - Use snake_case filenames (e.g., `birthday_celebration_pop.mp3`).

3. **Register in `catalog.json`**:
   Add or update the track entry with full metadata:
   ```json
   {
     "id": "birthday_celebration_pop",
     "title": "Birthday Celebration Pop",
     "category": "birthday",
     "description": "Upbeat pop rhythm with joyful piano and festive energy.",
     "filename": "birthday_celebration_pop.mp3",
     "source": "Free Music Archive / Musopen / Incompetech",
     "license": "CC-BY 4.0",
     "license_url": "https://creativecommons.org/licenses/by/4.0/",
     "attribution": "Artist Name, Title, licensed under CC-BY 4.0",
     "commercial_use": true,
     "restrictions": "Requires attribution in video credits."
   }
   ```

4. **FFmpeg Verification**:
   Verify container integrity and decode compatibility:
   ```bash
   ffmpeg -i <path_to_audio_file> -f null -
   ```
