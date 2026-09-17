# tkinter와 관련 모듈들을 임포트합니다. GUI 애플리케이션을 만들기 위해 필요합니다.
# json, os, re, threading, datetime, hashlib, logging 등의 모듈을 임포트합니다.
#   데이터 처리, 파일 시스템 접근, 정규표현식, 멀티스레딩, 날짜 시간 처리, 해시 함수, 로깅 등 다양한 기능을 제공합니다.

# pip install edge-tts pygame SpeechRecognition requests langdetect

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import json, os, re, threading, datetime, hashlib, logging, queue, asyncio, time
from collections import defaultdict
import logging.handlers
from pathlib import Path

# langdetect 라이브러리를 임포트하여 텍스트 언어를 감지합니다.
#   DetectorFactory.seed 값을 설정하여 감지 결과가 일관되게 나오도록 합니다.
# 언어 감지 라이브러리
from langdetect import detect, DetectorFactory
DetectorFactory.seed = 0  # 감지 결과 일관성 유지

# 로깅 핸들러를 설정하여 로그 파일을 생성하고 관리합니다.
#   로그 파일은 5MB까지 저장되며, 최대 3개의 백업 파일을 유지합니다.
# 로깅 설정
log_handler = logging.handlers.RotatingFileHandler(
#    f"languageai_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
    f"languageai.log",
    maxBytes=5 * 1024 * 1024,
    backupCount=3
)
log_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(log_handler)

# [최적화 ⑥] 번역/TTS/STT 요청마다 threading.Thread를 새로 만들면 매번 OS 스레드를
# 새로 생성/소멸시키는 비용이 듭니다. 아래 _DaemonThreadPool은 데몬 스레드 몇 개를
# 미리 띄워두고 큐로 작업을 넘겨 재사용합니다.
#
# concurrent.futures.ThreadPoolExecutor 대신 이렇게 직접 구현한 이유는,
# ThreadPoolExecutor는 프로그램 종료 시 진행 중인 작업이 끝날 때까지 기다리는
# atexit 훅이 있어서, STT처럼 최대 8초까지 블로킹될 수 있는 작업이 남아있으면
# 창을 닫아도 프로세스가 몇 초간 종료되지 않을 수 있기 때문입니다.
# 워커 스레드를 daemon=True로 두면 기존과 동일하게 프로그램 종료 시 즉시 함께
# 종료됩니다.
class _DaemonThreadPool:
    def __init__(self, max_workers=4):
        self._q = queue.Queue()
        for _ in range(max_workers):
            threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        while True:
            func, args, kwargs = self._q.get()
            try:
                func(*args, **kwargs)
            except Exception as e:
                logger.error(f"백그라운드 작업 오류: {e}")
            finally:
                self._q.task_done()

    def submit(self, func, *args, **kwargs):
        self._q.put((func, args, kwargs))

# 번역/TTS 재생/음성 인식 등 백그라운드 작업에서 공용으로 사용하는 스레드 풀
EXECUTOR = _DaemonThreadPool(max_workers=4)

# required_packages 딕셔너리를 사용하여 필요한 패키지들의 설치 여부를 확인합니다.
# 필수 패키지 설치 여부 확인
required_packages = {
    "speech_recognition": False,
    "pygame": False,
    "requests": False,
    "langdetect": True,
    "edge_tts": False
}

# [최적화 ①] speech_recognition, pygame, requests, edge_tts는 원래 함수 호출 시점마다
# 함수 내부에서 반복적으로 import 되었습니다. sys.modules에 캐시되어 있어 두 번째
# 호출부터는 느리지 않지만, 호출될 때마다 불필요한 속성 조회/바이트코드 실행이
# 발생합니다. 여기서 한 번만 import하고 아래에서는 모듈 전역 변수(sr, pygame,
# requests)를 그대로 재사용합니다. 패키지가 없으면 해당 이름은 None으로 남습니다.
sr = None
pygame = None
requests = None
edge_tts = None

# check_packages 함수를 통해 각 패키지가 설치되어 있는지 확인하고, 설치되지 않은 패키지는 로그에 에러를 남깁니다.
def check_packages():
    global sr, pygame, requests, edge_tts
    try:
        import speech_recognition as sr
        required_packages["speech_recognition"] = True
    except ImportError:
        logger.error("speech_recognition 패키지 없음")
        messagebox.showerror("패키지 오류", "speech_recognition 패키지가 설치되지 않았습니다.")
        
    try:
        import pygame
        required_packages["pygame"] = True
    except ImportError as e:
        logger.error(f"pygame 패키지 없음: {e}")
        messagebox.showerror("패키지 오류", f"pygame 패키지가 설치되지 않았습니다: {e}")

        
    try:
        import requests
        required_packages["requests"] = True
    except ImportError:
        logger.error("requests 패키지 없음")
        messagebox.showerror("패키지 오류", "requests 패키지가 설치되지 않았습니다.")

    try:
        import edge_tts
        required_packages["edge_tts"] = True
    except ImportError:
        logger.error("edge-tts 패키지 없음")
        messagebox.showerror("패키지 오류", "edge-tts 패키지가 설치되지 않았습니다.")


check_packages()

# ------------------------------------------------------------------------
# LANG_CONFIG(지원 언어 정보), UI_TEXTS(다국어 UI 문구) 딕셔너리는
# 더 이상 이 파이썬 파일 안에 직접 정의하지 않고,
# 이 스크립트와 같은 폴더에 있는 MultiLanguageAI.json 파일에서 읽어옵니다.
#
# 지원 언어를 추가/삭제하거나 UI 문구를 수정하고 싶을 때는
# 이 .py 코드를 건드릴 필요 없이 MultiLanguageAI.json 파일만 수정하면 됩니다.
# (코드와 데이터를 분리하여 유지보수성을 높이기 위한 구조입니다.)
# ------------------------------------------------------------------------

# 이 스크립트 파일이 위치한 폴더를 기준으로 설정 파일 경로를 지정합니다.
# (실행 파일(exe)로 패키징된 경우에도 같은 폴더의 json을 찾을 수 있도록 함)
BASE_DIR = Path(__file__).resolve().parent
LANG_CONFIG_FILE = BASE_DIR / "MultiLanguageAI.json"

# load_language_config 함수는 MultiLanguageAI.json 파일을 읽어
# LANG_CONFIG(지원 언어 정보)와 UI_TEXTS(다국어 UI 문구)를 반환합니다.
def load_language_config(path: Path):
    """
    외부 설정 파일(MultiLanguageAI.json)에서 LANG_CONFIG와 UI_TEXTS를 읽어옵니다.

    - 파일이 존재하지 않는 경우
    - JSON 형식이 잘못된 경우
    - "languages" 또는 "ui_texts" 키가 없거나 비어 있는 경우

    위 상황에서는 로그를 남기고 사용자에게 오류 메시지를 보여준 뒤
    프로그램을 종료합니다. (언어/문구 데이터 없이는 정상 동작이 불가능하므로)
    """
    if not path.exists():
        msg = (
            f"설정 파일을 찾을 수 없습니다:\n{path}\n\n"
            "프로그램과 같은 폴더에 MultiLanguageAI.json 파일이 있는지 확인해 주세요."
        )
        logger.critical(msg)
        try:
            messagebox.showerror("설정 파일 오류", msg)
        except Exception:
            pass
        raise SystemExit(1)

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        lang_config = data["languages"]
        ui_texts = data["ui_texts"]
        if not lang_config or not ui_texts:
            raise ValueError("languages 또는 ui_texts 항목이 비어 있습니다.")
        return lang_config, ui_texts
    except Exception as e:
        msg = f"설정 파일을 읽는 중 오류가 발생했습니다:\n{path}\n\n{e}"
        logger.critical(msg)
        try:
            messagebox.showerror("설정 파일 오류", msg)
        except Exception:
            pass
        raise SystemExit(1)

# 지원 언어 설정(LANG_CONFIG)과 다국어 UI 문구(UI_TEXTS)를 외부 JSON 파일에서 로드합니다.
LANG_CONFIG, UI_TEXTS = load_language_config(LANG_CONFIG_FILE)

# 콤보박스 표시용 언어 이름 -> 언어 코드 역매핑 디셔너리
NAME_TO_CODE = {info["name"]: code for code, info in LANG_CONFIG.items()}

# detect_text_language 함수는 주어진 텍스트의 언어를 감지하고, LANG_CONFIG에서 해당 언어의 정보와 코드를 반환합니다.
# 감지에 실패할 경우 기본으로 영어를 반환합니다.
def detect_text_language(text):
    """텍스트 언어를 자동 감지하여 LANG_CONFIG 설정과 언어 코드를 반환 (실패 시 기본 영어)"""
    try:
        code = detect(text).lower()
        if code in LANG_CONFIG:
            return LANG_CONFIG[code], code
    except Exception as e:
        logger.error(f"언어 감지 실패: {e}")
    return LANG_CONFIG["en"], "en"

# C 딕셔너리는 애플리케이션의 색상 테마를 정의합니다.
# 각 색상 이름에 해당하는 HEX 코드를 저장합니다.
# 색상 테마 설정
C = {
    "bg": "#0d1117",
    "bg2": "#161b22",
    "bg3": "#21262d",
    "border": "#30363d",
    "text": "#e6edf3",
    "text2": "#8b949e",
    "accent": "#58a6ff",
    "accent2": "#3fb950",
    "warn": "#d29922",
    "danger": "#f85149",
    "purple": "#bc8cff",
    "success": "#10b981",
    "primary": "#3b82f6"
}

# DATA_DIR과 CACHE_DIR 변수는 애플리케이션의 데이터와 캐시 파일을 저장할 디렉토리를 정의합니다.
# os.getenv를 사용하여 운영 체제의 APPDATA 경로를 가져오거나 기본으로 사용자의 홈 디렉토리를 사용합니다.
# 데이터 디렉토리 설정
DATA_DIR = Path(os.getenv("APPDATA", Path.home())) / "MultiLangAI"
CACHE_DIR = DATA_DIR / "cache"
DATA_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_FILE = DATA_DIR / "history.json"          # 예전 형식 (전체를 하나의 JSON 배열로 저장) - 마이그레이션용으로만 사용
HISTORY_JSONL_FILE = DATA_DIR / "history.jsonl"    # 새 형식 (한 줄에 기록 하나, append-only)
TRANSLATION_CACHE_FILE = DATA_DIR / "translation_cache.json"

# load_json 함수는 주어진 경로의 JSON 파일을 읽어와 파싱합니다. 파일이 존재하지 않을 경우 기본값을 반환합니다.
# JSON 파일 로드 및 저장 함수
def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except Exception as e:
        logger.error(f"JSON 로드 실패: {e}")
        return default

# save_json 함수는 주어진 데이터를 JSON 형식으로 파일에 저장합니다. ensure_ascii=False를 사용하여 유니코드 문자를 그대로 저장합니다.
def save_json(path, data):
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error(f"JSON 저장 실패: {e}")

# [최적화 ⑧] 학습 기록(history)은 예전에는 STT를 한 번 시도할 때마다
# save_json(HISTORY_FILE, self.history)로 리스트 전체를 다시 직렬화해서 파일에
# 통째로 다시 썼습니다. 기록이 오래 쌓일수록(수천 건) 매번 전체 재작성 비용이
# 커지므로, 한 줄에 기록 하나씩 저장하는 JSON Lines(.jsonl) 형식으로 바꾸고
# "새 기록 추가"는 파일 끝에 한 줄만 덧붙이는 append로 처리합니다.
# (기록을 통째로 다시 써야 하는 "삭제"의 경우에만 전체 재작성을 사용합니다.)
def load_history():
    """
    학습 기록을 불러옵니다.
    - history.jsonl(신규 형식)이 있으면 그것을 한 줄씩 읽어 사용합니다.
    - history.jsonl이 없고 예전 history.json(배열 전체 저장 방식)만 있다면
      한 번 읽어들여 history.jsonl로 마이그레이션합니다.
    """
    history = []
    if HISTORY_JSONL_FILE.exists():
        try:
            with HISTORY_JSONL_FILE.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        history.append(json.loads(line))
        except Exception as e:
            logger.error(f"history.jsonl 로드 실패: {e}")
    elif HISTORY_FILE.exists():
        history = load_json(HISTORY_FILE, [])
        save_history_full(history)
        logger.info("기존 history.json을 history.jsonl로 마이그레이션했습니다.")
    return history

def append_history_entry(entry):
    """새 학습 기록 한 건을 파일 끝에 추가합니다 (전체 재작성 없음)."""
    try:
        with HISTORY_JSONL_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.error(f"학습 기록 추가 실패: {e}")

def save_history_full(history):
    """기록 삭제처럼 전체를 다시 써야 하는 경우에만 사용하는 전체 재작성 함수."""
    try:
        with HISTORY_JSONL_FILE.open("w", encoding="utf-8") as f:
            for entry in history:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.error(f"학습 기록 저장 실패: {e}")

# clean_cache 함수는 캐시 디렉토리에서 30일 이상 지난 MP3 파일을 삭제합니다. 이를 통해 불필요한 파일 공간을 절약할 수 있습니다.
# 캐시 청소 함수
def clean_cache():
    now = datetime.datetime.now()
    for file in CACHE_DIR.glob("*.mp3"):
        file_mtime = datetime.datetime.fromtimestamp(file.stat().st_mtime)
        if (now - file_mtime).days > 30:
            try:
                file.unlink()
                logger.info(f"캐시 파일 제거: {file}")
            except Exception as e:
                logger.error(f"캐시 제거 실패: {e}")

# [최적화 ④] 정규식을 매 호출마다 컴파일하지 않도록 모듈 로드 시 한 번만 컴파일해 둡니다.
#   - _WORD_CLEAN_RE: calc_score(발음 채점, STT마다 호출)에서 문장부호 제거용
#   - _SENTENCE_SPLIT_RE: load_text(문장 분리)에서 사용 (크메르어/미얀마어 구분기호 포함)
_WORD_CLEAN_RE = re.compile(r"[^\w\s]")
_SENTENCE_SPLIT_RE = re.compile(r'[.!?\n\u17D4\u104B]')

# calc_score 함수는 목표 문장과 발음된 문장 간의 유사도를 계산합니다. 두 문장에서 일치하는 단어의 비율을 백분율로 반환합니다.
# 목표 문장과 발음된 문장 간의 유사도 계산 함수
def calc_score(target, said):
    t = _WORD_CLEAN_RE.sub("", target.lower()).split()
    s = _WORD_CLEAN_RE.sub("", said.lower()).split()
    # [최적화 ⑤] 리스트(t) 대상 in 검사는 단어마다 O(n) 선형 탐색이 됩니다.
    # set으로 바꾸면 단어당 O(1) 조회가 되어 문장이 길어질수록 유리합니다.
    t_set = set(t)
    matching_words = sum(1 for w in s if w in t_set)
    return int((matching_words / len(t)) * 100) if t else 0

# translate_google 함수는 Google Translate API를 사용하여 텍스트를 번역합니다.
# translation_cache 딕셔너리를 사용하여 번역 결과를 캐싱하여 반복적인 번역 요청을 줄입니다.
# requests 패키지가 설치되어 있지 않으면 에러 메시지를 반환합니다.
# source_lang과 target_lang이 같으면 원본 텍스트를 그대로 반환합니다.
# cache_key를 생성하여 이미 번역된 텍스트가 캐시에 존재하면 해당 결과를 반환합니다.
# requests.get을 사용하여 Google Translate API에 요청을 보내고, 응답을 JSON 형태로 파싱합니다.
# 번역된 텍스트를 추출하여 캐싱하고 반환합니다. 번역 결과가 없으면 기본 메시지를 반환합니다.
# 예외 발생 시 로그에 에러를 남기고 에러 메시지를 반환합니다.

# [최적화 ②] 번역 캐시를 메모리 dict뿐 아니라 디스크(JSON)에도 저장합니다.
# 기존에는 프로그램을 재시작하면 캐시가 사라져 같은 문장을 다시 번역 API에
# 요청했지만, 이제 TRANSLATION_CACHE_FILE에서 이전 캐시를 불러온 뒤 시작하고
# 새 번역 결과가 생길 때마다 파일에 반영해 세션 간에도 재사용합니다.
translation_cache = load_json(TRANSLATION_CACHE_FILE, {})

# [최적화 ③] requests.get()을 호출마다 새로 만들면 매번 TCP/TLS 연결을 새로
# 맺습니다. 모듈 전역 Session을 하나 재사용하면 연결이 유지(keep-alive)되어
# 반복 번역 요청의 지연시간이 줄어듭니다.
SESSION = None
if required_packages["requests"]:
    SESSION = requests.Session()
    SESSION.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})

# ------------------------------------------------------------------------
# [버그수정] "429 Client Error: Too Many Requests" 대응
#
# translate.googleapis.com(GTX)은 구글의 정식 공개 API가 아니라, 브라우저의
# 웹 번역 기능이 쓰는 비공식 엔드포인트를 그대로 호출하는 것입니다. 공식
# API가 아니다 보니 짧은 시간에 요청이 몰리면(예: 말레이어->베트남어->
# 인도네시아어처럼 여러 언어 버튼을 연달아 누르는 경우) 요청을 보낸 IP 전체를
# 일정 시간 동안 차단(429)합니다. 어떤 언어로 번역을 시도했는지와 무관하게,
# 한 번 429가 걸리면 그 이후의 모든 번역 요청이 함께 실패하는 것이 정상적인
# 동작입니다(질문에서 언어는 말레이어 등인데 오류 메시지에 ja->ko가 찍힌 것도
# 이 때문입니다 - 직전에 다른 언어로 호출했던 요청이 429를 유발한 것입니다).
#
# 해결책 두 가지를 함께 적용합니다:
#   1) 요청 사이 최소 간격을 두어(스로틀)애초에 429가 덜 걸리게 합니다.
#   2) 그래도 429가 나면, 즉시 포기하지 않고 점점 길어지는 대기시간을 두고
#      자동으로 재시도합니다.
# ------------------------------------------------------------------------
_TRANSLATE_MIN_INTERVAL_SEC = 1.2    # 번역 요청 사이 최소 간격(초)
_TRANSLATE_MAX_RETRIES = 3           # 429가 나도 재시도할 최대 횟수
_TRANSLATE_RETRY_BACKOFF_SEC = 3.0   # 재시도 대기시간의 기준값(시도마다 배로 증가)

_translate_lock = threading.Lock()
_last_translate_call_ts = [0.0]  # 클로저에서 값을 바꿀 수 있도록 리스트로 감쌈

_TRANSLATION_LANG_MAP = {
    "zh-cn": "zh-CN",
    "zh-tw": "zh-TW",
}

def _normalize_translation_lang(code):
    """번역 서비스가 인식하는 언어 코드로 변환합니다."""
    normalized = str(code or "").strip()
    return _TRANSLATION_LANG_MAP.get(normalized.lower(), normalized)

def _throttle_translate_call():
    """구글 번역 엔드포인트에 너무 잦은 요청이 몰리지 않도록 최소 간격을 둡니다."""
    with _translate_lock:
        now = time.monotonic()
        wait = _TRANSLATE_MIN_INTERVAL_SEC - (now - _last_translate_call_ts[0])
        if wait > 0:
            time.sleep(wait)
        _last_translate_call_ts[0] = time.monotonic()

# MyMemory는 Google GTX가 요청 제한(429)을 반환할 때 사용하는 무료 대체
# 번역 경로입니다. 별도 API 키가 필요하지 않지만 일일 사용량 제한이 있습니다.
def _translate_mymemory(text, source_lang, target_lang):
    response = SESSION.get(
        "https://api.mymemory.translated.net/get",
        params={
            "q": text,
            "langpair": f"{source_lang}|{target_lang}",
        },
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("responseStatus") != 200:
        raise RuntimeError(data.get("responseDetails") or "MyMemory 번역 실패")
    translated_text = (data.get("responseData") or {}).get("translatedText", "").strip()
    if not translated_text:
        raise RuntimeError("MyMemory가 빈 번역 결과를 반환했습니다.")
    return translated_text

def _translate_google_chrome(text, source_lang, target_lang):
    """Chrome 번역용 Google 엔드포인트를 사용한 보조 경로입니다."""
    response = SESSION.get(
        "https://clients5.google.com/translate_a/t",
        params={
            "client": "dict-chrome-ex",
            "sl": source_lang,
            "tl": target_lang,
            "q": text,
        },
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=10,
    )
    response.raise_for_status()
    # 이 엔드포인트는 Content-Type의 charset을 잘못 표시하는 경우가
    # 있어 response.text 대신 UTF-8 원문을 명시적으로 디코딩합니다.
    data = json.loads(response.content.decode("utf-8"))
    if not isinstance(data, list) or not data or not str(data[0]).strip():
        raise RuntimeError("Google Chrome 번역 결과가 비어 있습니다.")
    return str(data[0]).strip()

# Google Translate GTX API를 우선 사용하고, 요청 제한이나 네트워크 오류가
# 발생하면 MyMemory로 전환하여 source_lang -> target_lang 번역을 수행합니다.
def translate_google(text, source_lang="en", target_lang="ko"):
    if not required_packages["requests"]:
        return "(requests 패키지 없음)"

    source_lang = _normalize_translation_lang(source_lang)
    target_lang = _normalize_translation_lang(target_lang)
    if source_lang.lower() == target_lang.lower():
        return text

    cache_key = f"{source_lang}:{target_lang}:{text}"
    if cache_key in translation_cache:
        return translation_cache[cache_key]

    url = "https://translate.googleapis.com/translate_a/single"
    params = {
        "client": "gtx",
        "sl": source_lang,
        "tl": target_lang,
        "dt": "t",
        "q": text
    }

    for attempt in range(1, _TRANSLATE_MAX_RETRIES + 1):
        _throttle_translate_call()
        try:
            r = SESSION.get(url, params=params, timeout=5)

            if r.status_code == 429:
                logger.warning(
                    f"구글 번역 429(요청 제한) 발생; MyMemory로 전환합니다."
                )
                break

            r.raise_for_status()
            res_json = r.json()

            translated_parts = [
                segment[0] for segment in res_json[0]
                if segment and isinstance(segment, list) and len(segment) > 0 and segment[0]
            ]
            translated_text = "".join(translated_parts)

            if not translated_text:
                translated_text = "(번역 결과 없음)"

            translation_cache[cache_key] = translated_text
            save_json(TRANSLATION_CACHE_FILE, translation_cache)
            return translated_text
        except Exception as e:
            logger.warning(f"Google 번역 실패: {e}; MyMemory로 전환합니다.")
            break

    try:
        translated_text = _translate_google_chrome(text, source_lang, target_lang)
        translation_cache[cache_key] = translated_text
        save_json(TRANSLATION_CACHE_FILE, translation_cache)
        return translated_text
    except Exception as e:
        logger.warning(f"Google Chrome 번역 실패: {e}; MyMemory로 전환합니다.")

    try:
        translated_text = _translate_mymemory(text, source_lang, target_lang)
        translation_cache[cache_key] = translated_text
        save_json(TRANSLATION_CACHE_FILE, translation_cache)
        return translated_text
    except Exception as e:
        logger.error(f"Google/MyMemory 번역 오류: {e}")
        return "(번역에 실패했습니다. 인터넷 연결 또는 번역 서비스 사용량 제한을 확인해 주세요.)"

# safe_pygame_init 함수는 Pygame의 mixer 모듈을 초기화합니다.

# Pygame을 안전하게 초기화하는 함수
def safe_pygame_init():
    if not required_packages["pygame"]:
        logger.error("pygame 패키지 없음")
        return False
    try:
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        return True
    except Exception as e:
        logger.error(f"Pygame 초기화 실패: {e}")
        return False

# ------------------------------------------------------------------------
# 언어 코드별 Microsoft Edge TTS 음성입니다.
# MultiLanguageAI.json의 기존 gtts 값은 호환성을 위해 그대로 사용합니다.
# ------------------------------------------------------------------------
EDGE_TTS_VOICE_MAP = {
    "ko": "ko-KR-HyunsuMultilingualNeural",
    "en": "en-US-JennyNeural",
    "ja": "ja-JP-NanamiNeural",
    "zh-cn": "zh-CN-XiaoxiaoNeural",
    "zh-tw": "zh-TW-HsiaoChenNeural",
    "th": "th-TH-PremwadeeNeural",
    "vi": "vi-VN-HoaiMyNeural",
    "id": "id-ID-GadisNeural",
    "tl": "fil-PH-AngeloNeural",
    "ms": "ms-MY-YasminNeural",
    "km": "km-KH-SreymomNeural",
    "lo": "lo-LA-KeomanyNeural",
    "my": "my-MM-NilarNeural",
}

# ------------------------------------------------------------------------
# [원인 확인] "문장 듣기"는 실패하고 "문장 번역"은 성공하는 이유
#
# edge-tts의 우즈베크어 음성(uz-UZ-SardorNeural)은 현재 우즈베키스탄의 공식
# 문자인 "라틴 문자" 우즈베크어만 인식합니다("No audio was received" 오류로
# 확인됨). 그런데:
#   - "문장 듣기": 사용자가 불러온 원본 문장이 키릴 문자로 쓰인 우즈베크어
#     (예: "Менюни кўрсатинга оласизми")인 경우, edge-tts가 이를 인식하지
#     못해 오디오를 반환하지 않습니다.
#   - "문장 번역": Google 번역 API가 우즈베크어로 번역할 때는 기본적으로
#     라틴 문자("Menyuni ko'rsatinga olasizmi?")로 결과를 주기 때문에
#     문제없이 재생됩니다.
#
# 해결: language_code가 "uz"이고 텍스트에 키릴 문자가 섞여 있으면, edge-tts로
# 보내기 직전에 표준 우즈베크어 키릴->라틴 변환표로 자동 변환합니다.
# 화면에 표시되는 원본 문장이나 캐시 파일명(해시)은 그대로 두고, 오직
# TTS 엔진에 실제로 전달하는 텍스트만 내부적으로 바꿉니다.
# ------------------------------------------------------------------------
_CYRILLIC_RE = re.compile(r'[\u0400-\u04FF]')

# 표준 우즈베크어(1995년 라틴 문자 표기법 기준) 키릴 -> 라틴 1:1 대응표.
# "е/Е"는 문맥에 따라 e/ye로 갈리므로 아래 _uzbek_cyrillic_to_latin()에서
# 별도로 처리하고, 이 표에는 포함하지 않습니다.
_UZ_CYR2LAT_MAP = {
    "А": "A", "а": "a", "Б": "B", "б": "b", "В": "V", "в": "v",
    "Г": "G", "г": "g", "Д": "D", "д": "d",
    "Ж": "J", "ж": "j", "З": "Z", "з": "z", "И": "I", "и": "i",
    "Й": "Y", "й": "y", "К": "K", "к": "k", "Л": "L", "л": "l",
    "М": "M", "м": "m", "Н": "N", "н": "n", "О": "O", "о": "o",
    "П": "P", "п": "p", "Р": "R", "р": "r", "С": "S", "с": "s",
    "Т": "T", "т": "t", "У": "U", "у": "u", "Ф": "F", "ф": "f",
    "Х": "X", "х": "x", "Ц": "Ts", "ц": "ts", "Ч": "Ch", "ч": "ch",
    "Ш": "Sh", "ш": "sh", "Щ": "Sh", "щ": "sh",
    "Ъ": "ʼ", "ъ": "ʼ", "Ы": "I", "ы": "i", "Ь": "", "ь": "",
    "Э": "E", "э": "e", "Ю": "Yu", "ю": "yu", "Я": "Ya", "я": "ya",
    "Ё": "Yo", "ё": "yo",
    # 우즈베크어 고유 문자
    "Ў": "Oʻ", "ў": "oʻ", "Қ": "Q", "қ": "q",
    "Ғ": "Gʻ", "ғ": "gʻ", "Ҳ": "H", "ҳ": "h",
}
_UZ_VOWELS = set("аеёиоуыэюяАЕЁИОУЫЭЮЯ")

def _uzbek_cyrillic_to_latin(text):
    """
    키릴 문자로 쓰인 우즈베크어를 표준 라틴 문자 표기법으로 변환합니다.
    edge-tts의 uz-UZ 음성이 라틴 문자만 인식하기 때문에 필요합니다.

    "е/Е"는 어두이거나 모음 뒤에서는 "ye/Ye"로, 자음 뒤에서는 "e/E"로
    바뀌는 표준 규칙을 적용하고, 그 외 글자는 1:1 대응표를 사용합니다.
    (예: "Менюни кўрсатинга оласизми" -> "Menyuni koʻrsatinga olasizmi")
    """
    result = []
    prev_char = None
    for ch in text:
        if ch in ("е", "Е"):
            is_word_start = prev_char is None or not prev_char.isalpha()
            is_after_vowel = prev_char is not None and prev_char in _UZ_VOWELS
            if is_word_start or is_after_vowel:
                result.append("Ye" if ch == "Е" else "ye")
            else:
                result.append("E" if ch == "Е" else "e")
        else:
            result.append(_UZ_CYR2LAT_MAP.get(ch, ch))
        prev_char = ch
    return "".join(result)

# 언어별 "edge-tts 전송 직전 텍스트 전처리" 함수 매핑. 필요하다면 다른
# 키릴/라틴 혼용 언어(카자흐어 등)를 추가하고 싶을 때 여기만 확장하면 됩니다.
_TTS_TEXT_PREPROCESSORS = {
    "uz": lambda t: _uzbek_cyrillic_to_latin(t) if _CYRILLIC_RE.search(t) else t,
}

def _prepare_tts_text(text, language_code):
    """edge-tts로 보내기 직전, 필요하다면 언어별 텍스트 전처리를 적용합니다."""
    preprocessor = _TTS_TEXT_PREPROCESSORS.get(language_code)
    return preprocessor(text) if preprocessor else text

class TTSUnavailableError(Exception):
    """edge-tts에서 사용할 음성을 찾을 수 없을 때 발생시키는 예외."""
    def __init__(self, language_code):
        self.language_code = language_code
        super().__init__(f"'{language_code}' 언어는 edge-tts 음성이 없습니다.")

# edge-tts 합성 시 재시도 횟수와 재시도 간 대기시간(초).
# 웹소켓 연결 하나로 음성을 스트리밍 받는 방식이라 네트워크 상태에 따라
# 가끔 일시적으로 실패(NoAudioReceived, WebSocketError 등)할 수 있으므로,
# 한 번 실패했다고 바로 포기하지 않고 몇 번 더 시도합니다.
_EDGE_TTS_MAX_ATTEMPTS = 3
_EDGE_TTS_RETRY_DELAY_SEC = 1.0

def _synthesize_with_edge_tts(text, voice, path):
    """edge-tts를 사용해 텍스트를 mp3 파일로 저장합니다 (동기 래퍼 + 재시도 + 원자적 쓰기).

    edge-tts는 비동기(asyncio) API만 제공하므로, 워커 스레드 안에서
    asyncio.run()으로 새 이벤트 루프를 만들어 실행합니다. 이 함수는 항상
    별도의 백그라운드 스레드(EXECUTOR)에서만 호출되므로 메인 스레드의
    tkinter 이벤트 루프와 충돌하지 않습니다.

    [버그수정] 기존 코드는 communicate.save(str(path))로 최종 캐시 경로에
    바로 저장했습니다. 그런데 저장 도중 네트워크가 끊기는 등 실패가 나면
    "크기가 0이거나 불완전한 mp3 파일"이 캐시 경로에 그대로 남아버립니다.
    이후에는 synthesize_tts_to_file()이 "cache_path.exists() == True"만
    보고 이미 캐시된 것으로 착각해 그 깨진 파일을 계속 재생하려다
    실패하는 문제가 있었습니다(재시도할 방법이 없어 매번 "TTS 재생 실패"만
    반복). 이를 막기 위해 임시 파일(.part)에 먼저 저장한 뒤, 성공적으로
    끝나고 파일 크기가 0보다 클 때만 최종 캐시 경로로 옮깁니다(os.replace,
    원자적 이동). 실패 시에는 임시 파일을 지워서 다음 재생 시도 때 다시
    합성을 시도할 수 있게 합니다.
    """
    tmp_path = path.with_name(path.name + ".part")

    async def _run():
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(str(tmp_path))

    last_error = None
    for attempt in range(1, _EDGE_TTS_MAX_ATTEMPTS + 1):
        try:
            asyncio.run(_run())
            if not tmp_path.exists() or tmp_path.stat().st_size == 0:
                raise RuntimeError("edge-tts가 빈 오디오를 반환했습니다.")
            os.replace(str(tmp_path), str(path))  # 성공했을 때만 최종 캐시 파일 생성
            return
        except Exception as e:
            last_error = e
            logger.warning(
                f"edge-tts 합성 실패 (시도 {attempt}/{_EDGE_TTS_MAX_ATTEMPTS}, voice={voice}): {e}"
            )
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            if attempt < _EDGE_TTS_MAX_ATTEMPTS:
                time.sleep(_EDGE_TTS_RETRY_DELAY_SEC)

    # 모든 재시도가 실패한 경우, 마지막 오류를 그대로 올립니다.
    raise last_error

def synthesize_tts_to_file(text, language_code, cache_path):
    """
    문장을 음성 파일(mp3)로 합성하여 cache_path에 저장합니다.

    - cache_path에 이미 유효한(크기 0보다 큰) 파일이 있으면 아무 작업도
      하지 않습니다. 만약 과거 실패로 인해 크기가 0인 손상된 캐시
      파일이 남아있다면, 이를 자동으로 지우고 다시 합성합니다
      (예전 버전에서 이미 생성된 깨진 캐시도 스스로 복구됩니다).
    - language_code에 대응하는 edge-tts 음성을 사용합니다.
    - edge-tts가 설치되어 있지 않거나 음성 매핑이 없으면
      TTSUnavailableError를 발생시킵니다.

    성공 시 True를 반환합니다.
    """
    if cache_path.exists():
        if cache_path.stat().st_size > 0:
            return True
        # 크기 0인 손상된 캐시 파일 -> 삭제 후 재합성
        logger.warning(f"손상된 캐시 파일 발견, 삭제 후 재생성: {cache_path}")
        try:
            cache_path.unlink()
        except Exception:
            pass

    if not required_packages.get("edge_tts"):
        raise TTSUnavailableError(language_code)

    normalized_code = language_code.lower()
    edge_voice = EDGE_TTS_VOICE_MAP.get(normalized_code)
    if not edge_voice:
        raise TTSUnavailableError(language_code)

    tts_text = _prepare_tts_text(text, normalized_code)
    _synthesize_with_edge_tts(tts_text, edge_voice, cache_path)
    return True

# [버그수정] "문장 번역" 버튼(play_translated_tts)은 번역된 텍스트를 재생하고,
# "문장 듣기" 버튼(play_tts)은 원본 문장을 재생합니다. 텍스트가 다르면
# 캐시 파일명(mp3 해시)도 서로 다르기 때문에, 과거(이번 수정 이전) 버전에서
# "문장 듣기"로 특정 문장을 먼저 재생해 본 적이 있다면 그때 생성된 손상된
# 캐시 파일이 남아 있을 수 있습니다. 이 손상된 캐시는 크기가 0이 아닐 수도
# 있어서 synthesize_tts_to_file()의 "크기 0 파일 자동 삭제" 로직만으로는
# 걸러지지 않고, pygame이 재생을 시도하는 단계에서 비로소 오류가 납니다.
# 반면 번역 텍스트는 해시가 달라 항상 새로 합성되므로 문제없이 재생됩니다.
#
# 이를 근본적으로 해결하기 위해, "합성 -> 재생"을 한 번에 묶고 재생이
# 실패하면 캐시를 지운 뒤 다시 합성부터 재시도하는 공통 함수로 통합합니다.
# play_tts와 play_translated_tts가 모두 이 함수를 사용합니다.
def _synthesize_and_play(text, language_code, cache_path, max_attempts=2):
    """텍스트를 TTS로 합성한 뒤 pygame으로 재생합니다.

    캐시된 mp3가 손상되어 pygame이 재생하지 못하면, 그 캐시 파일을 지우고
    처음부터(합성부터) 다시 시도합니다(최대 max_attempts회).
    해당 언어 자체가 TTS 미지원인 경우(ValueError, TTSUnavailableError)는
    재시도 없이 즉시 예외를 그대로 올립니다(호출부에서 안내 메시지 처리).
    """
    last_error = None
    for attempt in range(1, max_attempts + 1):
        # ValueError / TTSUnavailableError는 여기서 발생하면 재시도하지 않고 바로 전파됩니다.
        synthesize_tts_to_file(text, language_code, cache_path)
        try:
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()
            pygame.mixer.music.load(str(cache_path))
            pygame.mixer.music.play()
            return
        except Exception as e:
            last_error = e
            logger.warning(
                f"캐시된 오디오 재생 실패 (시도 {attempt}/{max_attempts}), "
                f"캐시를 지우고 재합성합니다: {e}"
            )
            if cache_path.exists():
                try:
                    cache_path.unlink()
                except Exception:
                    pass
    raise last_error

# styled_label 함수는 주어진 스타일을 적용한 tk.Label을 생성합니다. 텍스트 크기, 굵기, 색상 등을 파라미터로 받습니다.
# 스타일이 적용된 GUI 컴포넌트 생성 함수
def styled_label(parent, text="", size=11, bold=False, color=None, **kw):
    return tk.Label(
        parent,
        text=text,
        font=("Segoe UI", size, "bold" if bold else "normal"),
        fg=color or C["text"],
        bg=kw.pop("bg", C["bg2"]),
        **kw
    )

# styled_button 함수는 주어진 스타일을 적용한 tk.Button을 생성합니다.
# 버튼의 종류(primary, secondary, success, danger)에 따라 배경색과 전경색을 다르게 설정합니다.
def styled_button(parent, text, command, kind="secondary", **kw):
    colors = {
        "primary": (C["primary"], "#ffffff"),
        "secondary": (C["bg3"], C["text"]),
        "success": (C["success"], "#ffffff"),
        "danger": (C["danger"], "#ffffff")
    }
    bg, fg = colors.get(kind, colors["secondary"])
    return tk.Button(
        parent,
        text=text,
        command=command,
        bg=bg,
        fg=fg,
        relief="flat",
        cursor="hand2",
        padx=12,
        pady=6,
        font=("Segoe UI", 10, "normal"),
        activebackground=bg,
        activeforeground=fg,
        **kw
    )

# sort_column 함수는 아직 구현되지 않았으며, 나중에 구현될 수 있는 기능입니다. 현재는 pass로 비어 있습니다.
def sort_column(col):
    pass

# StatsWindow 클래스는 학습 성취도 분석 창을 나타내는 tk.Toplevel을 상속받습니다.
# 초기화 시 부모 창과 학습 기록(history)을 받아 설정합니다.
# 학습 성취도 분석 창을 관리하는 클래스
class StatsWindow(tk.Toplevel):
    def __init__(self, parent, history):
        super().__init__(parent)
        self.title("나의 학습 성취도 분석")
        self.geometry("800x600")
        self.configure(bg=C["bg"])
        self.history = history
        # [최적화 ⑨] _get_avg/draw_graph가 매번 self.history 전체를 순회하는 대신,
        # 날짜별 점수 목록을 한 번만 인덱싱해두고 재사용합니다.
        # (기록이 많이 쌓일수록 전체 재순회 대비 효과가 커집니다.)
        self._score_by_date = self._build_date_index()
        self._setup_ui()
        clean_cache()

    # _build_date_index 메서드는 self.history를 날짜(문자열) -> 점수 리스트로 인덱싱합니다.
    def _build_date_index(self):
        index = defaultdict(list)
        for h in self.history:
            index[h["date"]].append(h["score"])
        return index

    # _setup_ui 메서드는 학습 성취도 분석 창의 UI를 구성합니다. 그래프, 학습 성취도 요약, 학습 기록 목록, 버튼 등을 포함합니다.
    # canvas는 그래프를 그리는 데 사용되는 tk.Canvas입니다.
    # info_frame은 오늘, 주간, 월간 학습 성취도를 표시하는 레이블들을 포함합니다.
    # tree는 학습 기록 목록을 표시하는 ttk.Treeview입니다. 날짜와 정확도를 열로 가지며, 스크롤바를 통해 목록을 스크롤할 수 있습니다.

    def _setup_ui(self):
        styled_label(
            self,
            "📊 최근 7일 학습 성취도",
            size=14,
            bold=True,
            bg=C["bg"]
        ).pack(pady=15)

        self.canvas = tk.Canvas(
            self,
            width=700,
            height=300,
            bg=C["bg2"],
            highlightthickness=0
        )
        self.canvas.pack(pady=10)

        self.info_frame = tk.Frame(self, bg=C["bg"], pady=10)
        self.info_frame.pack(fill="x", padx=20)

        self.lbl_day = styled_label(
            self.info_frame,
            "",
            color=C["accent"],
            bg=C["bg"]
        )
        self.lbl_day.pack(side="left", expand=True, padx=10)

        self.lbl_week = styled_label(
            self.info_frame,
            "",
            color=C["accent2"],
            bg=C["bg"]
        )
        self.lbl_week.pack(side="left", expand=True, padx=10)

        self.lbl_month = styled_label(
            self.info_frame,
            "",
            color=C["purple"],
            bg=C["bg"]
        )
        self.lbl_month.pack(side="left", expand=True, padx=10)

        list_frame = tk.Frame(self, bg=C["bg2"])
        list_frame.pack(fill="both", expand=True, padx=20, pady=10)

        self.tree = ttk.Treeview(
            list_frame,
            columns=("date", "score"),
            show="headings",
            height=8
        )
        self.tree.heading("date", text="날짜", command=lambda: sort_column("date"))
        self.tree.heading("score", text="정확도", command=lambda: sort_column("score"))
        self.tree.column("date", width=150, anchor="center")
        self.tree.column("score", width=100, anchor="center")

        sb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        button_frame = tk.Frame(self, bg=C["bg"])
        button_frame.pack(fill="x", padx=20, pady=10)

        styled_button(
            button_frame,
            "🗑 선택 항목 삭제",
            self.delete_selected,
            kind="danger"
        ).pack(side="left", padx=5)

        styled_button(
            button_frame,
            "🔄 새로 고침",
            self.refresh_list,
            kind="primary"
        ).pack(side="left", padx=5)

        self.refresh_list()

    # _update_summary 메서드는 오늘, 주간, 월간 학습 성취도를 계산하여 레이블에 표시합니다.
    def _update_summary(self):
        today = datetime.date.today()
        self.lbl_day.config(text=f"오늘: {self._get_avg(today, today)}%")
        self.lbl_week.config(text=f"주간: {self._get_avg(today - datetime.timedelta(days=7), today)}%")
        self.lbl_month.config(text=f"월간: {self._get_avg(today - datetime.timedelta(days=30), today)}%")

    # _get_avg 메서드는 주어진 날짜 범위 내의 평균 정확도를 계산합니다.
    def _get_avg(self, start, end):
        # 예전에는 self.history 전체를 훑으며 날짜를 파싱/비교했지만,
        # 이제 날짜별로 미리 인덱싱된 self._score_by_date에서 범위 내 날짜만
        # 조회합니다. (day/week/month 요약 3번 호출 시 전체 스캔 3회 -> 인덱스 조회로 대체)
        scores = []
        d = start
        while d <= end:
            scores.extend(self._score_by_date.get(str(d), []))
            d += datetime.timedelta(days=1)
        return sum(scores) // len(scores) if scores else 0

    # draw_graph 메서드는 최근 7일 동안의 학습 성취도를 그래프 형태로 그립니다. 각 날짜별로 평균 정확도를 막대그래프로 표시합니다.
    def draw_graph(self):
        self.canvas.delete("all")
        today = datetime.date.today()
        days = [(today - datetime.timedelta(days=i)) for i in range(6, -1, -1)]
        p = 40
        w, h = 700 - (p * 2), 300 - (p * 2)

        for i, d in enumerate(days):
            scores = self._score_by_date.get(str(d), [])
            avg = sum(scores) // len(scores) if scores else 0
            x0 = p + i * (w / 7) + 10
            y0 = 300 - p - (avg * h / 100)
            x1 = x0 + (w / 7) - 15
            y1 = 300 - p

            color = C["accent"] if avg > 0 else C["bg3"]
            self.canvas.create_rectangle(x0, y0, x1, y1, fill=color, outline="")
            self.canvas.create_text(
                (x0 + x1) / 2,
                y0 - 12,
                text=f"{avg}%",
                fill=C["text"],
                font=("Segoe UI", 8)
            )
            self.canvas.create_text(
                (x0 + x1) / 2,
                y1 + 15,
                text=d.strftime("%m/%d"),
                fill=C["text2"],
                font=("Segoe UI", 8)
            )

    # refresh_list 메서드는 학습 기록 목록을 갱신합니다. tree 위젯을 초기화하고 새로운 데이터를 삽입한 후 그래프와 요약을 갱신합니다.
    def refresh_list(self):
        # 삭제 등으로 self.history가 바뀌었을 수 있으므로 인덱스를 다시 만듭니다.
        self._score_by_date = self._build_date_index()

        for i in self.tree.get_children():
            self.tree.delete(i)
        for e in reversed(self.history):
            self.tree.insert("", "end", values=(e['date'], f"{e['score']}%"))

        self.draw_graph()
        self._update_summary()

    # delete_selected 메서드는 사용자가 선택한 학습 기록을 삭제합니다. 
    # 삭제 전 사용자에게 확인 메시지를 표시하고, 확인 시 선택한 항목을 삭제한 후 학습 기록 파일을 갱신합니다.
    def delete_selected(self):
        selected = self.tree.selection()
        if not selected:
            return

        if not messagebox.askyesno("삭제 확인", "선택한 기록을 삭제하시겠습니까?"):
            return

        indices = [self.tree.index(item) for item in selected]

        for idx in sorted(indices, reverse=True):
            if idx < len(self.history):
                del self.history[idx]

        # 삭제는 임의 위치의 항목이 빠지는 경우라 전체 재작성이 필요합니다.
        # (반면 새 기록 추가는 append_history_entry로 파일 끝에 한 줄만 덧붙입니다.)
        save_history_full(self.history)
        self.refresh_list()

# MultiLangAIApp 클래스는 tk.Tk를 상속받아 애플리케이션의 메인 창을 만듭니다.
# __init__ 메서드에서 애플리케이션의 제목, 크기, 배경색을 설정합니다.
# self.history는 학습 기록을 저장하는 리스트입니다.
#   load_json 함수를 사용하여 HISTORY_FILE에서 기존 기록을 불러옵니다.
# self.sentences는 현재 학습 중인 문장들을 저장하는 리스트이며, self.curr_idx는 현재 학습 중인 문장의 인덱스입니다.
# self.current_lang_info와 self.current_lang_code는 현재 선택된 언어의 정보와 코드를 저장합니다. 기본값은 영어입니다.
# self.trans_target_code는 번역 대상 언어 코드를 저장하며, 기본값은 한국어입니다.
# self.audio_ready는 Pygame의 mixer 모듈이 성공적으로 초기화되었는지 여부를 저장합니다.
# _setup_style 메서드는 애플리케이션의 스타일을 설정합니다. ttk.Style을 사용하여 콤보박스의 배경색, 전경색, 화살표 색상 등을 설정합니다.
# 드롭다운 팝업 리스트박스의 폰트와 색상을 설정하여 사용자 경험을 향상시킵니다.

# 메인 애플리케이션 클래스
class MultiLangAIApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MultiLangAI - 다국어 발음 학습 및 성취도 분석")
        self.geometry("800x750")
        self.configure(bg=C["bg"])
        self.history = load_history()
        self.sentences = []
        self.curr_idx = 0
        
        self.current_lang_info = LANG_CONFIG["en"]
        self.current_lang_code = "en"

        # 번역 대상 언어 (LANG_CONFIG 내 언어로 제한, 기본값은 한국어)
        self.trans_target_code = "ko"
        
        self.audio_ready = safe_pygame_init()
        self._setup_style()
        self._setup_ui()

        # [최적화 ⑩] clean_cache()는 CACHE_DIR의 모든 mp3 파일을 glob + stat으로
        # 훑는 작업이라, 캐시가 많이 쌓인 상태에서 __init__ 중에 동기 실행하면
        # 창이 화면에 뜨기 전 잠깐 멈춘 것처럼 보일 수 있습니다.
        # self.after()로 mainloop 시작 직후로 미뤄서, 창이 먼저 반응 가능한
        # 상태가 된 뒤에 백그라운드성으로 캐시 정리가 이루어지게 합니다.
        self.after(500, clean_cache)

    def _setup_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        
        # 콤보박스 다크 테마 커스텀 및 글자 대비 향상
        style.configure(
            "TCombobox",
            fieldbackground=C["bg3"],
            background=C["bg3"],
            foreground=C["text"],
            darkcolor=C["border"],
            lightcolor=C["border"],
            bordercolor=C["border"],
            arrowcolor=C["accent"],
            padding=5
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", C["bg3"])],
            foreground=[("readonly", C["text"])],
            selectbackground=[("readonly", C["bg3"])],
            selectforeground=[("readonly", C["accent"])]
        )
        
        # 드롭다운 팝업 리스트박스 폰트 및 색상 설정
        self.option_add("*TCombobox*Listbox.font", ("Segoe UI", 11))
        self.option_add("*TCombobox*Listbox.background", C["bg3"])
        self.option_add("*TCombobox*Listbox.foreground", C["text"])
        self.option_add("*TCombobox*Listbox.selectBackground", C["primary"])
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

    # _setup_ui 메서드는 애플리케이션의 주요 UI 요소를 설정합니다.

    def _setup_ui(self):
        # input_container는 헤더 라벨, 언어 선택 콤보박스, 번역 대상 언어 선택 콤보박스, 텍스트 입력 필드, 버튼 등을 포함하는 프레임입니다.
        input_container = tk.Frame(self, bg=C["bg"], pady=15)
        input_container.pack(fill="x", padx=30)

        # header_row는 헤더 라벨과 언어 선택 콤보박스를 포함하는 프레임입니다.
        # 헤더 라벨과 언어 선택 드롭다운 콤보박스 행
        header_row = tk.Frame(input_container, bg=C["bg"])
        header_row.pack(fill="x", pady=5)

        # lbl_title과 lbl_select_lang은 각각 "학습할 외국어 텍스트:"와 "언어 선택:" 라벨을 생성합니다.
        self.lbl_title = styled_label(
            header_row,
            "학습할 외국어 텍스트:",
            size=12,
            bold=True,
            bg=C["bg"]
        )
        self.lbl_title.pack(side="left")

        self.lbl_select_lang = styled_label(
            header_row,
            "언어 선택:",
            size=11,
            bold=True,
            color=C["accent"],
            bg=C["bg"]
        )
        self.lbl_select_lang.pack(side="left", padx=(15, 8))

        # combo_values는 콤보박스에 표시될 언어 목록을 생성합니다. "자동 감지" 옵션을 포함합니다.
        combo_values = ["🌐 자동 감지"] + [info["name"] for info in LANG_CONFIG.values()]
        
        self.combo_lang = ttk.Combobox(
            header_row,
            values=combo_values,
            state="readonly",
            width=30,
            font=("Segoe UI", 11, "bold")
        )
        # combo_lang은 언어 선택 콤보박스를 생성하고, 선택된 언어가 변경될 때 on_language_change 메서드가 호출되도록 이벤트를 연결합니다.
        self.combo_lang.set("🌐 자동 감지")
        self.combo_lang.pack(side="left", fill="x", expand=True)
        
        # 언어 콤보박스 변경 이벤트 연결
        self.combo_lang.bind("<<ComboboxSelected>>", self.on_language_change)

        # trans_lang_row는 번역 대상 언어 선택 콤보박스를 포함하는 프레임입니다.
        # 번역 대상 언어 선택 행 (LANG_CONFIG에 정의된 언어로만 제한)
        trans_lang_row = tk.Frame(input_container, bg=C["bg"])
        trans_lang_row.pack(fill="x", pady=5)

        # lbl_trans_lang은 "번역 대상 언어:" 라벨을 생성합니다.
        self.lbl_trans_lang = styled_label(
            trans_lang_row,
            "번역 대상 언어:",
            size=11,
            bold=True,
            color=C["purple"],
            bg=C["bg"]
        )
        self.lbl_trans_lang.pack(side="left", padx=(0, 8))

        # trans_combo_values는 번역 대상 언어 선택 콤보박스에 표시될 언어 목록을 생성합니다.
        trans_combo_values = [info["name"] for info in LANG_CONFIG.values()]

        # combo_trans_lang은 번역 대상 언어 선택 콤보박스를 생성하고, 
        #   선택된 언어가 변경될 때 on_trans_lang_change 메서드가 호출되도록 이벤트를 연결합니다.
        self.combo_trans_lang = ttk.Combobox(
            trans_lang_row,
            values=trans_combo_values,
            state="readonly",
            width=30,
            font=("Segoe UI", 11)
        )
        # 기본 번역 대상 언어는 한국어로 설정
        self.combo_trans_lang.set(LANG_CONFIG[self.trans_target_code]["name"])
        self.combo_trans_lang.pack(side="left", fill="x", expand=True)

        # 번역 대상 언어 콤보박스 변경 이벤트 연결
        self.combo_trans_lang.bind("<<ComboboxSelected>>", self.on_trans_lang_change)

        # txt_input은 텍스트 입력 필드를 생성합니다.
        self.txt_input = tk.Entry(
            input_container,
            bg=C["bg3"],
            fg=C["text"],
            font=("Segoe UI", 12),
            relief="flat",
            insertbackground=C["text"],
            bd=10
        )
        self.txt_input.pack(fill="x", pady=10)
        
        # btn_row는 버튼들을 포함하는 프레임입니다.
        btn_row = tk.Frame(input_container, bg=C["bg"])
        btn_row.pack(fill="x")

        # btn_load_text와 btn_load_file은 각각 "텍스트 불러오기"와 "파일 불러오기" 버튼을 생성합니다.
        #   각 버튼이 클릭될 때 load_text와 load_file 메서드가 호출됩니다.
        self.btn_load_text = styled_button(
            btn_row,
            "텍스트 불러오기",
            self.load_text,
            kind="primary"
        )
        self.btn_load_text.pack(side="left", fill="x", expand=True, padx=5)

        self.btn_load_file = styled_button(
            btn_row,
            "파일 불러오기",
            self.load_file
        )
        self.btn_load_file.pack(side="left", fill="x", expand=True, padx=5)

        # main_frame은 학습 문장, 번역 결과, 학습 결과, 제어 버튼 등을 포함하는 프레임입니다.
        main_frame = tk.Frame(
            self,
            bg=C["bg2"],
            highlightbackground=C["border"],
            highlightthickness=1
        )
        main_frame.pack(fill="both", expand=True, padx=30, pady=10)
        
        # lbl_progress는 현재 학습 상태를 표시하는 라벨입니다.
        self.lbl_progress = styled_label(
            main_frame,
            "문장을 준비해 주세요.",
            size=10,
            color=C["text2"]
        )
        self.lbl_progress.pack(pady=15, anchor="center")

        # lbl_sentence는 현재 학습 중인 문장을 표시하는 라벨입니다.
        self.lbl_sentence = styled_label(
            main_frame,
            "Ready to Study",
            size=18,
            bold=True,
            wraplength=700,
            justify="center"
        )
        self.lbl_sentence.pack(pady=30, padx=20)

        # lbl_trans은 번역된 문장을 표시하는 라벨입니다.
        self.lbl_trans = styled_label(
            main_frame,
            "",
            size=12,
            color=C["accent"],
            wraplength=700,
            justify="center"
        )
        self.lbl_trans.pack(pady=10)

        # lbl_result는 음성 인식 결과를 표시하는 라벨입니다.
        self.lbl_result = styled_label(
            main_frame,
            "결과가 표시됩니다.",
            size=11,
            color=C["text2"]
        )
        self.lbl_result.pack(pady=20)

        # ctrl_frame은 제어 버튼들을 포함하는 프레임입니다.
        ctrl_frame = tk.Frame(main_frame, bg=C["bg2"])
        ctrl_frame.pack(side="bottom", pady=30)

        # btn_tts, btn_mic, btn_trans, btn_prev, btn_next는 
        #   각각 "문장 듣기", "발음 연습", "문장 번역", "이전 문장", "다음 문장" 버튼을 생성합니다.
        #   각 버튼이 클릭될 때 해당 메서드가 호출됩니다.
        self.btn_tts = styled_button(
            ctrl_frame,
            "🔊 문장 듣기",
            self.play_tts,
            kind="primary"
        )
        self.btn_tts.pack(side="left", padx=10)

        self.btn_mic = styled_button(
            ctrl_frame,
            "🎤 발음 연습",
            self.start_stt,
            kind="success"
        )
        self.btn_mic.pack(side="left", padx=10)

        self.btn_trans = styled_button(
            ctrl_frame,
            "🔍 문장 번역",
            self.show_translation
        )
        self.btn_trans.pack(side="left", padx=10)

        self.btn_prev = styled_button(
            ctrl_frame,
            "⬅ 이전 문장",
            self.prev_sentence
        )
        self.btn_prev.pack(side="left", padx=10)

        self.btn_next = styled_button(
            ctrl_frame,
            "➡ 다음 문장",
            self.next_sentence
        )
        self.btn_next.pack(side="left", padx=10)

        self.btn_stats = styled_button(
            self,
            "📊 학습 성취도 분석",
            self.show_stats,
            kind="secondary"
        )
        self.btn_stats.pack(pady=20)

    # update_ui_language 메서드는 선택한 언어에 맞게 UI 버튼 및 레이블 텍스트를 일괄 업데이트합니다.
    def update_ui_language(self):
        """선택한 언어에 맞게 UI 버튼 및 레이블 텍스트 일괄 업데이트"""
        lang_code = self.current_lang_code if self.current_lang_code in UI_TEXTS else "ko"
        texts = UI_TEXTS[lang_code]

        self.lbl_title.config(text=texts["title_label"])
        self.lbl_select_lang.config(text=texts["lang_select_label"])
        self.lbl_trans_lang.config(text=texts["trans_lang_label"])
        self.btn_load_text.config(text=texts["btn_load_text"])
        self.btn_load_file.config(text=texts["btn_load_file"])
        self.btn_tts.config(text=texts["btn_tts"])
        self.btn_mic.config(text=texts["btn_mic"])
        self.btn_trans.config(text=texts["btn_trans"])
        self.btn_prev.config(text=texts["btn_prev"])
        self.btn_next.config(text=texts["btn_next"])
        self.btn_stats.config(text=texts["btn_stats"])
        
        if not self.sentences:
            self.lbl_progress.config(text=texts["default_progress"])
            self.lbl_result.config(text=texts["default_result"])

    # on_language_change 메서드는 언어 드롭다운 변경 시 UI 및 내부 언어 상태를 업데이트합니다.
    def on_language_change(self, event=None):
        """언어 드롭다운 변경 시 UI 및 내부 언어 상태 업데이트"""
        raw = self.txt_input.get().strip()
        
        selected = self.combo_lang.get()
        if selected == "🌐 자동 감지":
            if raw:
                self.current_lang_info, self.current_lang_code = detect_text_language(raw)
            else:
                self.current_lang_code = "ko"
        else:
            lang_code = NAME_TO_CODE.get(selected, "en")
            self.current_lang_info = LANG_CONFIG[lang_code]
            self.current_lang_code = lang_code

        # 선택한 언어로 버튼 글자 업데이트
        self.update_ui_language()

        # 기존 불러온 문장이 있다면 상태 갱신
        if raw and self.sentences:
            self.update_sentence()

    # on_trans_lang_change 메서드는 번역 대상 언어 드롭다운 변경 시 내부 상태를 업데이트합니다.
    def on_trans_lang_change(self, event=None):
        """번역 대상 언어 드롭다운 변경 시 내부 상태 업데이트 (LANG_CONFIG 언어로 제한됨)"""
        selected = self.combo_trans_lang.get()
        self.trans_target_code = NAME_TO_CODE.get(selected, "en")

        # 번역 대상 언어가 바뀌면 이전에 표시된 번역 결과는 더 이상 유효하지 않으므로 초기화
        self.lbl_trans.config(text="")

    # load_text 메서드는 입력된 텍스트를 기반으로 문장을 불러옵니다.
    def load_text(self):
        raw = self.txt_input.get().strip()
        if not raw:
            messagebox.showinfo("알림", "텍스트를 입력해 주세요.")
            return

        # 크메르어(\u17D4), 미얀마어(\u104B) 등 동남아시아 문장 구분 기호 분할
        self.sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(raw) if s.strip()]
        self.curr_idx = 0
        
        # 언어 감지 또는 선택된 언어 적용
        selected = self.combo_lang.get()
        if selected == "🌐 자동 감지":
            self.current_lang_info, self.current_lang_code = detect_text_language(raw)
        else:
            lang_code = NAME_TO_CODE.get(selected, "en")
            self.current_lang_info = LANG_CONFIG[lang_code]
            self.current_lang_code = lang_code
            
        self.update_ui_language()
        self.update_sentence()

    # load_file 메서드는 선택된 파일을 읽어와 텍스트 입력 필드에 삽입합니다.
    def load_file(self):
        path = filedialog.askopenfilename(filetypes=[("Text files", "*.txt")])
        if path:
            try:
                text = Path(path).read_text(encoding="utf-8")
                self.txt_input.delete(0, tk.END)
                self.txt_input.insert(tk.END, text)
                self.load_text()
            except FileNotFoundError:
                logger.error(f"파일을 찾을 수 없습니다: {path}")
                messagebox.showerror("오류", f"파일을 찾을 수 없습니다: {path}")
            except PermissionError:
                logger.error(f"파일 권한이 없습니다: {path}")
                messagebox.showerror("오류", f"파일 권한이 없습니다: {path}")
            except Exception as e:
                logger.error(f"파일 읽기 실패: {e}")
                messagebox.showerror("오류", f"파일을 읽을 수 없습니다: {e}")

    # update_sentence 메서드는 현재 문장을 UI에 표시하고 진행 상태를 업데이트합니다.
    def update_sentence(self):
        if 0 <= self.curr_idx < len(self.sentences):
            lang_name = self.current_lang_info["name"]
            
            self.lbl_sentence.config(text=self.sentences[self.curr_idx])
            self.lbl_progress.config(
                text=f"[{lang_name}] 문장 {self.curr_idx + 1} / {len(self.sentences)}"
            )
            # 언어 변경 또는 문장 변경 시 이전 번역 및 결과 라벨 초기화
            self.lbl_trans.config(text="")
            lang_code = self.current_lang_code if self.current_lang_code in UI_TEXTS else "ko"
            self.lbl_result.config(text=UI_TEXTS[lang_code]["default_result"], fg=C["text2"])

    # show_translation 메서드는 선택된 문장을 번역합니다.
    def show_translation(self):
        if not self.sentences:
            return

        # [최적화 ⑦] 번역 중 버튼을 잠깐 비활성화해서, 이전 요청이 끝나기 전에
        # 같은 문장을 여러 번 중복 요청하는 것을 막습니다.
        self.btn_trans.config(state="disabled")
        self.lbl_trans.config(text="🔍 번역 중...", fg=C["accent"])
        # [최적화 ⑥] threading.Thread를 매번 새로 만드는 대신 공용 스레드 풀에 작업만 넘깁니다.
        # [최적화 ⑦] 콜백이 도착했을 때 사용자가 이미 다른 문장으로 넘어갔다면 결과를
        # 화면에 반영하지 않도록, 요청 시점의 문장 인덱스를 함께 넘겨서 확인합니다.
        EXECUTOR.submit(self._translate_thread, self.curr_idx)

    # _translate_thread 메서드는 별도의 스레드에서 번역을 수행하고, 번역 결과를 UI에 업데이트합니다.
    def _translate_thread(self, request_idx):
        try:
            target_text = self.sentences[request_idx]
            target_lang = self.trans_target_code
            translated = translate_google(
                target_text,
                source_lang=self.current_lang_code,
                target_lang=target_lang
            )

            # 번역이 끝난 시점에 사용자가 이미 다른 문장으로 이동했다면
            # (요청 당시 인덱스와 현재 인덱스가 다르면) 화면 갱신을 건너뜁니다.
            if request_idx != self.curr_idx:
                return

            # 메인 스레드에서 UI 갱신
            self.after(0, lambda: self.lbl_trans.config(text=translated, fg=C["accent"]))
            
            # 번역이 완료되면 사용자가 선택한 언어로 자동 TTS 발음 재생
            if translated and not translated.startswith("("):
                self.play_translated_tts(translated, target_lang)
        except Exception as e:
            logger.error(f"번역 스레드 오류: {e}")
            if request_idx == self.curr_idx:
                self.after(0, lambda: self.lbl_trans.config(text="(번역 실패)", fg=C["danger"]))
        finally:
            self.after(0, lambda: self.btn_trans.config(state="normal"))

    # play_translated_tts 메서드는 번역된 문장을 사용자가 선택한 언어(lang_code, LANG_CONFIG 기준)로 자동 발음(TTS)해주는 함수입니다.
    def play_translated_tts(self, text, lang_code):
        """번역된 문장을 사용자가 선택한 언어(lang_code, LANG_CONFIG 기준)로 자동 발음(TTS)해주는 함수"""
        if not (self.audio_ready and text):
            return

        lang_info = LANG_CONFIG.get(lang_code, LANG_CONFIG["en"])
        language_code = lang_info["gtts"]
        cache_path = CACHE_DIR / f"{language_code}_{hashlib.md5(text.encode('utf-8')).hexdigest()}.mp3"

        # _play_audio 메서드는 별도의 스레드에서 edge-tts로 번역된 문장을 재생합니다.
        def _play_audio():
            try:
                _synthesize_and_play(text, language_code, cache_path)
            except TTSUnavailableError:
                logger.warning(f"번역 음성 미지원 언어: {language_code} ({lang_info['name']})")
                self.after(0, lambda: self.lbl_result.config(
                    text=f"⚠ 이 언어({lang_info['name']})는 발음 재생을 지원하지 않습니다.",
                    fg=C["warn"]
                ))
            except Exception as e:
                logger.error(f"번역 음성 재생 실패: {e}")

        EXECUTOR.submit(_play_audio)
    

    # play_tts 메서드는 현재 문장을 사용자가 선택한 언어(lang_code, LANG_CONFIG 기준)로 자동 발음(TTS)해주는 함수입니다.
    def play_tts(self):
        if not (self.audio_ready and self.sentences):
            return

        text = self.sentences[self.curr_idx]
        language_code = self.current_lang_info["gtts"]
        cache_path = CACHE_DIR / f"{language_code}_{hashlib.md5(text.encode('utf-8')).hexdigest()}.mp3"

        # [최적화 ⑦] 같은 문장에 대해 mp3 캐시 파일이 아직 없는 상태에서 버튼을
        # 연타하면, 두 스레드가 동시에 같은 파일에 gTTS 저장을 시도해 충돌할 수
        # 있습니다. 생성/재생이 끝날 때까지 버튼을 잠깐 비활성화해 이를 막습니다.
        self.btn_tts.config(state="disabled")

        # _play_audio 메서드는 별도의 스레드에서 edge-tts로 현재 문장을 재생합니다.
        def _play_audio():
            try:
                _synthesize_and_play(text, language_code, cache_path)
            except TTSUnavailableError:
                self.after(0, lambda: messagebox.showwarning(
                    "TTS 미지원", 
                    f"현재 언어({self.current_lang_info['name']})는 음성 듣기를 지원하지 않습니다."
                ))
            except Exception as e:
                # [버그수정] except 블록을 벗어나면 파이썬이 'e'를 자동으로 삭제하므로,
                # self.after()로 나중에 실행되는 lambda 안에서 e를 그대로 참조하면
                # NameError가 발생합니다. except 블록 안에서 메시지를 문자열로
                # 미리 만들어 둔 뒤(err_msg) lambda에서는 그 문자열만 사용합니다.
                err_msg = str(e)
                logger.error(f"TTS 재생 실패: {err_msg}")
                self.after(0, lambda: messagebox.showerror(
                    "오류",
                    f"TTS 재생 실패: {err_msg}\n\n(다시 시도하면 재합성됩니다. 계속 실패하면 인터넷 연결이나 방화벽/보안 프로그램이 edge-tts 접속을 막고 있지 않은지 확인해 주세요.)"
                ))
            finally:
                self.after(0, lambda: self.btn_tts.config(state="normal"))

        # [최적화 ⑥] threading.Thread를 매번 새로 만드는 대신 공용 스레드 풀에 작업만 넘깁니다.
        EXECUTOR.submit(_play_audio)

    # start_stt 메서드는 음성 인식을 시작합니다.
    def start_stt(self):
        if not self.sentences:
            return

        self.btn_mic.config(state="disabled")
        # [최적화 ⑥] threading.Thread를 매번 새로 만드는 대신 공용 스레드 풀에 작업만 넘깁니다.
        EXECUTOR.submit(self._listen_thread)

    # _listen_thread 메서드는 별도의 스레드에서 음성 인식을 수행하고, 인식 결과를 UI에 업데이트합니다.
    def _listen_thread(self):
        try:
            r = sr.Recognizer()
            r.pause_threshold = 2.0
            r.non_speaking_duration = 0.4
            
            stt_code = self.current_lang_info["stt"]

            with sr.Microphone() as source:
                r.adjust_for_ambient_noise(source, duration=0.5)
                self.btn_mic.config(text="● 지금 말씀하세요", fg=C["danger"])
                self.lbl_result.config(text="듣고 있습니다...", fg=C["accent"])

                audio = r.listen(source, timeout=8, phrase_time_limit=25)
                self.btn_mic.config(text="분석 중...")

                said = r.recognize_google(audio, language=stt_code)
                score = calc_score(self.sentences[self.curr_idx], said)

                entry = {
                    "date": str(datetime.date.today()),
                    "score": score
                }
                self.history.append(entry)
                # 새 기록 한 건만 파일 끝에 덧붙입니다 (전체 재작성 없음).
                append_history_entry(entry)

                self.after(0, lambda: self.lbl_result.config(
                    text=f"정확도: {score}% | '{said}'",
                    fg=C["accent2"]
                ))

        except Exception as e:
            # [버그수정] lambda가 self.after()로 나중에 실행되는 시점엔 이미
            # except 블록을 벗어나 'e'가 삭제된 뒤이므로, 문자열로 미리 저장해 둡니다.
            err_msg = str(e)
            logger.error(f"음성 인식 오류: {err_msg}")
            self.after(0, lambda: self.lbl_result.config(text=f"오류 발생: {err_msg}", fg=C["danger"]))
        finally:
            lang_code = self.current_lang_code if self.current_lang_code in UI_TEXTS else "ko"
            btn_mic_text = UI_TEXTS[lang_code]["btn_mic"]
            self.after(0, lambda: self.btn_mic.config(text=btn_mic_text, state="normal", fg="#ffffff"))

    # prev_sentence 메서드는 이전 문장을 표시합니다.
    def prev_sentence(self):
        if self.curr_idx > 0:
            self.curr_idx -= 1
            self.update_sentence()

    # next_sentence 메서드는 다음 문장을 표시합니다.
    def next_sentence(self):
        if self.curr_idx < len(self.sentences) - 1:
            self.curr_idx += 1
            self.update_sentence()

    # show_stats 메서드는 학습 성취도 분석 창을 표시합니다. 학습 기록이 없으면 알림 메시지를 표시합니다.
    def show_stats(self):
        if not self.history:
            messagebox.showinfo("알림", "학습 데이터가 없습니다.")
            return

        StatsWindow(self, self.history)

# main 함수는 MultiLangAIApp 애플리케이션을 실행하는 진입점입니다.
def main():
    app = MultiLangAIApp()
    app.mainloop()

# 프로그램이 직접 실행될 때 main 함수를 호출하여 애플리케이션을 시작합니다.
if __name__ == "__main__":
    main()