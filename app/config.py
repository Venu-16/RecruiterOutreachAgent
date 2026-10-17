from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")
DATA_INPUT_DIR = ROOT_DIR / "data" / "input"
DATA_OUTPUT_DIR = ROOT_DIR / "data" / "output"
SAMPLE_DIR = ROOT_DIR / "data" / "samples"
LOCAL_CANDIDATE_PROFILE = DATA_INPUT_DIR / "candidate_profile.json"
DATABASE_PATH = Path(os.getenv("OUTREACH_DATABASE", str(DATA_OUTPUT_DIR / "outreach.sqlite3"))).expanduser()
SENDING_ENABLED = os.getenv("OUTREACH_SENDING_ENABLED", "false").casefold() == "true"
API_TOKEN = os.getenv("OUTREACH_API_TOKEN", "")
MAX_DRAFT_LENGTH = int(os.getenv("OUTREACH_MAX_DRAFT_LENGTH", "2500"))
QUALIFIED_THRESHOLD = int(os.getenv("OUTREACH_QUALIFIED_THRESHOLD", "70"))
TARGET_ROLES = tuple(role.strip() for role in os.getenv(
	"OUTREACH_TARGET_ROLES", "AI Engineer,Machine Learning Engineer,LLM Engineer,Python Developer"
).split(",") if role.strip())