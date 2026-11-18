import json

transcript_path = r"C:\Users\shiva\.gemini\antigravity\brain\0a152a85-fc2e-43e1-9b67-aefeb8d42a73\.system_generated\logs\transcript.jsonl"
with open(transcript_path, "r", encoding="utf-8") as f:
    for line_idx, line in enumerate(f):
        data = json.loads(line)
        if data.get("step_index") in [3551, 3597, 3598]:
            print(f"\nStep {data.get('step_index')}:")
            print(data.get("content", "")[:1200])

