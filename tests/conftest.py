# SPDX-License-Identifier: Apache-2.0
import os
import sys

# Prevent Qt from requiring a display when running in CI/locally without one
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
