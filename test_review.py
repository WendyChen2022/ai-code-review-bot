"""Quick test: send a sample diff to Claude and print the review."""

from pathlib import Path
from dotenv import load_dotenv

# Load .env before importing review_bot so env vars are available at import time
load_dotenv(Path(__file__).parent / ".env", override=True)

from src.review_bot import analyze_code_diff

SAMPLE_DIFF = """\
+def calculate_discount(price, discount):
+    result = price / discount
+    return result
"""

if __name__ == "__main__":
    print("Sending diff to Claude for review...\n")
    review = analyze_code_diff(SAMPLE_DIFF)
    print(review)
