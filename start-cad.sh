#!/bin/sh
# Starts the Interim CAD server (Linux/macOS).
cd "$(dirname "$0")" && exec python3 manage.py serve
