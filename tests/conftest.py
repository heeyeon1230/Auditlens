import sys
from pathlib import Path

# 저장소 최상위 폴더를 import 경로에 추가해 'auditlens' 패키지를 찾을 수 있게 합니다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
