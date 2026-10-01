#!/usr/bin/env bash
# macOS / Linux 실행 스크립트
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "처음 실행이라 필요한 프로그램을 설치하고 있어요..."
  python3 -m venv .venv || { echo "Python 3.10 이상을 설치해주세요: https://www.python.org/downloads/"; exit 1; }
fi
.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt
.venv/bin/python -m app.main
