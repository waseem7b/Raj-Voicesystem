"""Test bootstrap.

`src/agent/ghl.py` reads the three GHL Private Integration Tokens out of the
environment at import time. The tests don't need real tokens — set placeholder
values before any test imports the module.
"""
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("GHL_MAIN_CRM_PIT", "test-main-pit")
os.environ.setdefault("GHL_TEXTS_CRM_1_PIT", "test-t1-pit")
os.environ.setdefault("GHL_TEXTS_CRM_2_PIT", "test-t2-pit")

# Webhook signature verification must be OFF for the unsigned requests these tests
# send. The secret legitimately lives in config/.env for real deploys; neutralize
# it here (before server.py's load_dotenv, which is override=False) so the suite is
# hermetic regardless of whether config/.env has it.
os.environ.setdefault("VAPI_WEBHOOK_SECRET", "")
