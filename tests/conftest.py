import os

# Prevent Qt from requiring a display when running in CI/locally without one
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
