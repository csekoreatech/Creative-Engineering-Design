# ============================================================================
# install_requirements.ps1
#
# MultiLanguageAI_gTTS.py 실행에 필요한 모듈을 자동으로 설치하는
# 순수 Windows(PowerShell) 스크립트입니다.
#
# [파이썬 표준 라이브러리 - 별도 설치 불필요]
#   tkinter, json, os, re, threading, datetime, hashlib, logging, pathlib
#   (Windows용 python.org 설치본은 tkinter가 기본 포함되어 있습니다)
#
# [pip로 설치해야 하는 외부 패키지]
#   langdetect          - 텍스트 언어 자동 감지
#   SpeechRecognition   - 음성 인식(마이크 발음 연습, sr 모듈)
#   gTTS                - Google TTS 음성 합성
#   pygame              - mp3 재생
#   requests            - 번역 API 등 HTTP 요청
#   PyAudio             - SpeechRecognition의 sr.Microphone() 마이크 입력 백엔드
#     (Windows는 PyPI에 미리 빌드된 wheel이 배포되어 있어
#      대부분 별도 컴파일러 없이 pip install만으로 설치됩니다)
#
# 사용법 (아래 둘 중 하나):
#   1) PowerShell에서:
#        cd 스크립트가있는폴더
#        Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#        .\install_requirements.ps1
#
#   2) CMD에서 (탐색기에서 install_requirements.bat 더블클릭도 가능):
#        install_requirements.bat
# ============================================================================

$ErrorActionPreference = "Continue"
$FailedItems = New-Object System.Collections.Generic.List[string]

# ---------------------------------------------------------------------------
# 로그 출력 함수
# ---------------------------------------------------------------------------
function Log-Info  { param([string]$Message) Write-Host "[정보] $Message" -ForegroundColor Green }
function Log-Warn  { param([string]$Message) Write-Host "[경고] $Message" -ForegroundColor Yellow }
function Log-Error { param([string]$Message) Write-Host "[오류] $Message" -ForegroundColor Red }

Write-Host "============================================================"
Write-Host " MultiLanguageAI 필수 모듈 자동 설치 스크립트 (Windows)"
Write-Host "============================================================"
Write-Host ""

# ---------------------------------------------------------------------------
# 1. 파이썬 실행 명령 확인 (py 런처 우선, 없으면 python)
# ---------------------------------------------------------------------------
$PythonCmd = $null

if (Get-Command py -ErrorAction SilentlyContinue) {
    $PythonCmd = "py"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $PythonCmd = "python"
}

if (-not $PythonCmd) {
    Log-Error "Python이 설치되어 있지 않거나 PATH에 등록되어 있지 않습니다."
    Log-Error "https://www.python.org/downloads/ 에서 Python 3를 설치한 뒤 다시 실행해 주세요."
    Log-Error "설치 시 'Add python.exe to PATH' 옵션을 반드시 체크해 주세요."
    Read-Host "엔터 키를 누르면 종료합니다"
    exit 1
}

$PythonVersion = & $PythonCmd --version 2>&1
Log-Info "Python 확인됨: $PythonVersion (명령: $PythonCmd)"

# ---------------------------------------------------------------------------
# 2. pip 존재 확인 및 업그레이드
# ---------------------------------------------------------------------------
& $PythonCmd -m pip --version *> $null
if ($LASTEXITCODE -ne 0) {
    Log-Warn "pip이 확인되지 않아 ensurepip으로 설치를 시도합니다..."
    & $PythonCmd -m ensurepip --upgrade
}

Log-Info "pip을 최신 버전으로 업그레이드합니다..."
& $PythonCmd -m pip install --upgrade pip *> $null

# ---------------------------------------------------------------------------
# 3. tkinter 확인 (Windows는 보통 기본 포함, 없으면 재설치 안내)
# ---------------------------------------------------------------------------
Log-Info "설치 확인 중: tkinter (표준 라이브러리)"
& $PythonCmd -c "import tkinter" *> $null
if ($LASTEXITCODE -eq 0) {
    Log-Info "  -> 사용 가능합니다."
} else {
    Log-Error "  -> tkinter를 찾을 수 없습니다."
    Log-Error "     python.org 설치 프로그램을 다시 실행하여 'tcl/tk and IDLE' 옵션을 체크한 뒤 Modify(수정)를 진행해 주세요."
    $FailedItems.Add("tkinter (Modify 설치 필요)")
}

# ---------------------------------------------------------------------------
# 4. pip 외부 패키지 설치 함수
# ---------------------------------------------------------------------------
function Install-PipPackage {
    param(
        [string]$PackageName,
        [string]$ImportName
    )

    Log-Info "설치 확인 중: $PackageName (import $ImportName)"

    & $PythonCmd -c "import $ImportName" *> $null
    if ($LASTEXITCODE -eq 0) {
        Log-Info "  -> 이미 설치되어 있습니다. 건너뜁니다."
        return
    }

    Log-Info "  -> 설치를 시작합니다: $PackageName"
    $logFile = Join-Path $env:TEMP "pip_install_$PackageName.log"
    & $PythonCmd -m pip install --upgrade $PackageName *> $logFile

    if ($LASTEXITCODE -eq 0) {
        Log-Info "  -> 설치 완료: $PackageName"
    } else {
        Log-Error "  -> 설치 실패: $PackageName (로그: $logFile)"
        $FailedItems.Add($PackageName)
    }
}

Log-Info "필요한 파이썬 패키지를 설치합니다..."
Write-Host ""

Install-PipPackage -PackageName "langdetect"        -ImportName "langdetect"
Install-PipPackage -PackageName "SpeechRecognition"  -ImportName "speech_recognition"
Install-PipPackage -PackageName "gTTS"               -ImportName "gtts"
Install-PipPackage -PackageName "edge-tts"           -ImportName "edge-tts"
Install-PipPackage -PackageName "pygame"             -ImportName "pygame"
Install-PipPackage -PackageName "requests"           -ImportName "requests"
Install-PipPackage -PackageName "PyAudio"            -ImportName "pyaudio"

# ---------------------------------------------------------------------------
# 5. PyAudio 설치 실패 시 대체 방법 (pipwin)
#    Windows에서 드물게 PyAudio wheel이 현재 파이썬 버전과 맞지 않는 경우가 있어
#    pipwin(비공식 Windows용 wheel 저장소)으로 재시도합니다.
# ---------------------------------------------------------------------------
if ($FailedItems.Contains("PyAudio")) {
    Log-Warn "PyAudio 일반 설치에 실패하여 pipwin으로 재시도합니다..."
    & $PythonCmd -m pip install pipwin *> $null
    & $PythonCmd -m pipwin install pyaudio *> $null

    & $PythonCmd -c "import pyaudio" *> $null
    if ($LASTEXITCODE -eq 0) {
        Log-Info "  -> pipwin을 통해 PyAudio 설치 완료"
        $FailedItems.Remove("PyAudio") | Out-Null
    } else {
        Log-Error "  -> pipwin으로도 PyAudio 설치에 실패했습니다."
        Log-Error "     https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio 에서"
        Log-Error "     자신의 Python 버전/아키텍처에 맞는 .whl 파일을 받아 수동 설치해 주세요."
        Log-Error "     예) pip install PyAudio-0.2.14-cp311-cp311-win_amd64.whl"
    }
}

# ---------------------------------------------------------------------------
# 6. requirements.txt 생성 (재현 가능한 환경 구성을 위해)
# ---------------------------------------------------------------------------
$ReqFile = Join-Path (Get-Location) "requirements.txt"
@"
langdetect
SpeechRecognition
gTTS
pygame
requests
PyAudio
"@ | Set-Content -Path $ReqFile -Encoding UTF8

Log-Info "$ReqFile 파일을 생성했습니다. (pip install -r requirements.txt 로도 재설치 가능)"

# ---------------------------------------------------------------------------
# 7. 결과 요약
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "================= 설치 결과 요약 ================="
if ($FailedItems.Count -eq 0) {
    Write-Host "모든 모듈이 정상적으로 설치(또는 이미 설치)되어 있습니다." -ForegroundColor Green
    Write-Host "이제 다음 명령으로 프로그램을 실행할 수 있습니다:"
    Write-Host "  $PythonCmd MultiLanguageAI_edge-tts-04.py"
} else {
    Write-Host "다음 항목의 설치에 실패했습니다:" -ForegroundColor Red
    foreach ($item in $FailedItems) {
        Write-Host "  - $item"
    }
    Write-Host ""
    Write-Host "PyAudio가 계속 실패한다면 아래를 확인해 주세요:"
    Write-Host "  1) 사용 중인 Python이 64비트인지 확인: $PythonCmd -c ""import struct;print(struct.calcsize('P')*8)"""
    Write-Host "  2) https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio 에서 whl 파일을 받아 수동 설치"
}
Write-Host "===================================================="
Write-Host ""
Read-Host "엔터 키를 누르면 종료합니다"
