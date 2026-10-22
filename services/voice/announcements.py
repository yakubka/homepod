"""Prepared announcements, the ids and texts GET /announcements serves."""

ANNOUNCEMENTS = [
    {"id": "ann_phones", "title": "Phones on silent",
     "text": "Please switch your phones to silent. The lecture is about to begin.",
     "category": "reminder", "language": "en-US", "duration_s": 5},
    {"id": "ann_break10", "title": "Ten minute break",
     "text": "We will take a ten minute break. The lecture continues at the next bell.",
     "category": "schedule", "language": "en-US", "duration_s": 6},
    {"id": "ann_evacuate", "title": "Evacuate the room",
     "text": "Please leave the room calmly by the nearest exit and follow the staff.",
     "category": "emergency", "language": "en-US", "duration_s": 6},
]

BY_ID = {item["id"]: item for item in ANNOUNCEMENTS}
