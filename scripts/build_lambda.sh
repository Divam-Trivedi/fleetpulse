#!/usr/bin/env bash
# Builds build/alert_lambda.zip with Linux wheels, so it works when built on a Mac.
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf build/lambda && mkdir -p build/lambda
pip install --quiet --target build/lambda \
  --platform manylinux2014_x86_64 --python-version 3.12 --implementation cp --only-binary=:all: \
  snowflake-connector-python cryptography
cp lambdas/alert_handler.py build/lambda/
(cd build/lambda && zip -qr ../alert_lambda.zip . -x "*.pyc" "*/__pycache__/*" "*.dist-info/RECORD")
ls -lh build/alert_lambda.zip
