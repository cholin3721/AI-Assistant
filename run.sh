#!/usr/bin/env bash
# macOS / Linux 실행 스크립트
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "처음 실행이라 필요한 프로그램을 설치하고 있어요..."
  python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' \
    || { echo "Python 3.10 이상을 설치해주세요: https://www.python.org/downloads/"; exit 1; }
  python3 -m venv .venv || { echo "Python 3.10 이상을 설치해주세요: https://www.python.org/downloads/"; exit 1; }
fi
# 필요한 프로그램 목록이 지난번 설치 때와 같으면 설치 확인을 건너뜀
if ! cmp -s requirements.txt .venv/requirements.installed; then
  .venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt \
    || { echo "설치 중 문제가 생겼어요. 인터넷 연결을 확인하고 다시 실행해주세요."; exit 1; }
  cp requirements.txt .venv/requirements.installed
fi
.venv/bin/python -m app.main
