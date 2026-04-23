import sys
from unittest.mock import MagicMock

# Mock anthropic globally for all tests
sys.modules["anthropic"] = MagicMock()