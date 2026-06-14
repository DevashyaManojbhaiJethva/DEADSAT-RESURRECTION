import os
import sys

# Ensure parent directory is in path and initialize mocks
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mock_oqs_nacl
