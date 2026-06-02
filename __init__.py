# Sets TF env vars before any module in this package imports TensorFlow.
# This file runs first whenever anything in `app` is imported.
import os
os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL",  "3")
