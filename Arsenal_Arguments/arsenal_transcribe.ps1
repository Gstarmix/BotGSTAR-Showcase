# =============================================================================
# arsenal_transcribe.ps1 — Wrapper Whisper pour Arsenal Intelligence Unit
#
# Appelle batch_transcribe_ultimate.ps1 (whisper_engine.ps1) avec les chemins
# Arsenal corrects. Deux passes :
#   Passe 1 : Videos simples  01_raw_videos → 02_whisper_transcripts
#   Passe 2 : Slides carrousel 01_raw_images/IG_*/  → 02_whisper_transcripts_carousels/IG_*/
#
# USAGE :
#   .\arsenal_transcribe.ps1                    # Mode normal (2 passes)
#   .\arsenal_transcribe.ps1 -Watch             # Mode surveillance continue
#   .\arsenal_transcribe.ps1 -Force             # Re-transcrire tout
#   .\arsenal_transcribe.ps1 -VideosOnly        # Passe 1 uniquement
#   .\arsenal_transcribe.ps1 -CarouselsOnly     # Passe 2 uniquement
#
# PREREQUIS :
#   - whisper_engine.ps1 dans le meme dossier
#   - pip install faster-whisper nvidia-cublas-cu12 nvidia-cudnn-cu12
# =============================================================================

[CmdletBinding()]
param(
    [string]$BasePath = "",

    [ValidateSet("tiny","base","small","medium","large-v2","large-v3")]
    [string]$Model = "large-v3",
    [string]$Lang = "fr",

    [ValidateSet("auto","cuda","cpu")]
    [string]$Device = "auto",
    [ValidateSet("auto","float16","int8_float16","int8","float32")]
    [string]$ComputeType = "auto",

    [switch]$Watch,
    [switch]$Force,
    [switch]$VideosOnly,
    [switch]$CarouselsOnly,

    [int]$PollSeconds = 15,
    [int]$PauseSeconds = 2
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# =============================================================================
# RESOLUTION DES CHEMINS
# =============================================================================

# Chemin de base Arsenal
if (-not $BasePath) {
    $BasePath = $env:ARSENAL_BASE_PATH
}
if (-not $BasePath) {
    # Auto-detect depuis le dossier du script
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    if (Test-Path (Join-Path $scriptDir "suivi_global.csv")) {
        $BasePath = $scriptDir
    } else {
        $BasePath = "C:\Users\Gstar\OneDrive\Documents\BotGSTAR\Arsenal_Arguments"
    }
}

# Chemins Arsenal
$VideoDir       = Join-Path $BasePath "01_raw_videos"
$ImageDir       = Join-Path $BasePath "01_raw_images"
$TranscriptDir  = Join-Path $BasePath "02_whisper_transcripts"
$CarouselTxDir  = Join-Path $BasePath "02_whisper_transcripts_carousels"
$LogDir         = Join-Path $BasePath "02_whisper_logs"

# Script moteur Whisper
$WhisperEngine = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "whisper_engine.ps1"
if (-not (Test-Path $WhisperEngine)) {
    # Fallback : meme dossier que BasePath
    $WhisperEngine = Join-Path $BasePath "whisper_engine.ps1"
}

# Verification
if (-not (Test-Path $WhisperEngine)) {
    Write-Host ""
    Write-Host "[ERREUR] whisper_engine.ps1 introuvable :" -ForegroundColor Red
    Write-Host "  $WhisperEngine" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Placez whisper_engine.ps1 dans le meme dossier que ce script." -ForegroundColor Gray
    exit 1
}

# Creer les dossiers si necessaire
foreach ($d in @($TranscriptDir, $CarouselTxDir, $LogDir)) {
    if (-not (Test-Path $d)) {
        New-Item -ItemType Directory -Path $d -Force | Out-Null
    }
}

# =============================================================================
# BANNER
# =============================================================================

function Write-ArsenalBanner {
    $banner = @"

    _                              _   _____                          _ _
   / \   _ __ ___  ___ _ __   __ _| | |_   _| __ __ _ _ __  ___  ___| (_)_ __   ___
  / _ \ | '__/ __|/ _ \ '_ \ / _`` | |   | || '__/ _`` | '_ \/ __|/ __| | | '_ \ / _ \
 / ___ \| |  \__ \  __/ | | | (_| | |   | || | | (_| | | | \__ \ (__| | | |_) |  __/
/_/   \_\_|  |___/\___|_| |_|\__,_|_|   |_||_|  \__,_|_| |_|___/\___|_|_| .__/ \___|
                                                                          |_|
"@
    Write-Host $banner -ForegroundColor Cyan
    Write-Host "  Arsenal Intelligence Unit — Whisper Transcription" -ForegroundColor White
    Write-Host ""
}

Clear-Host
Write-ArsenalBanner

Write-Host "  Configuration :" -ForegroundColor Yellow
Write-Host "    Base     : $BasePath" -ForegroundColor Gray
Write-Host "    Modele   : $Model" -ForegroundColor Gray
Write-Host "    Device   : $Device" -ForegroundColor Gray
Write-Host "    Compute  : $ComputeType" -ForegroundColor Gray
Write-Host "    Mode     : $(if ($Watch) { 'WATCH' } else { 'NORMAL' })" -ForegroundColor Gray
Write-Host ""

# Arguments communs pour le moteur
$commonArgs = @(
    "-Model", $Model,
    "-Lang", $Lang,
    "-Device", $Device,
    "-ComputeType", $ComputeType,
    "-PauseSeconds", $PauseSeconds
)

if ($Force) { $commonArgs += "-Force" }
if ($Watch) {
    $commonArgs += "-Watch"
    $commonArgs += "-PollSeconds"
    $commonArgs += $PollSeconds
}

# Compteurs globaux
$totalTranscribed = 0
$totalFailed = 0
$totalSkipped = 0

# =============================================================================
# PASSE 1 : VIDEOS SIMPLES
# =============================================================================

function Invoke-Pass1 {
    if ($CarouselsOnly) {
        Write-Host "  [SKIP] Passe 1 (videos simples) — mode -CarouselsOnly" -ForegroundColor DarkGray
        return
    }

    Write-Host "  ============================================" -ForegroundColor Cyan
    Write-Host "  PASSE 1 : Videos simples" -ForegroundColor Cyan
    Write-Host "  Source  : $VideoDir" -ForegroundColor Gray
    Write-Host "  Sortie  : $TranscriptDir" -ForegroundColor Gray
    Write-Host "  ============================================" -ForegroundColor Cyan
    Write-Host ""

    if (-not (Test-Path $VideoDir)) {
        Write-Host "  [SKIP] Dossier videos introuvable : $VideoDir" -ForegroundColor Yellow
        return
    }

    $videoCount = (Get-ChildItem -Path $VideoDir -File -Recurse |
        Where-Object { $_.Extension -in @(".mp4",".mkv",".webm",".mov",".m4v",".avi") }).Count

    if ($videoCount -eq 0) {
        Write-Host "  [SKIP] Aucune video dans $VideoDir" -ForegroundColor Yellow
        return
    }

    Write-Host "  $videoCount fichier(s) video detecte(s)" -ForegroundColor White

    $pass1Args = @(
        "-SrcDir", $VideoDir,
        "-OutRoot", $TranscriptDir,
        "-LogRoot", (Join-Path $LogDir "videos")
    ) + $commonArgs

    & $WhisperEngine @pass1Args

    Write-Host ""
    Write-Host "  Passe 1 terminee." -ForegroundColor Green
    Write-Host ""
}

# =============================================================================
# PASSE 2 : SLIDES VIDEO DE CARROUSELS
# =============================================================================

function Invoke-Pass2 {
    if ($VideosOnly) {
        Write-Host "  [SKIP] Passe 2 (carrousels) — mode -VideosOnly" -ForegroundColor DarkGray
        return
    }

    Write-Host "  ============================================" -ForegroundColor Cyan
    Write-Host "  PASSE 2 : Slides video de carrousels" -ForegroundColor Cyan
    Write-Host "  Source  : $ImageDir\IG_*\*.mp4" -ForegroundColor Gray
    Write-Host "  Sortie  : $CarouselTxDir\IG_*\" -ForegroundColor Gray
    Write-Host "  ============================================" -ForegroundColor Cyan
    Write-Host ""

    if (-not (Test-Path $ImageDir)) {
        Write-Host "  [SKIP] Dossier images introuvable : $ImageDir" -ForegroundColor Yellow
        return
    }

    # Trouver tous les dossiers IG_ contenant des videos
    $igDirs = Get-ChildItem -Path $ImageDir -Directory |
        Where-Object { $_.Name -like "IG_*" } |
        Where-Object {
            (Get-ChildItem -Path $_.FullName -File |
                Where-Object { $_.Extension -in @(".mp4",".mkv",".webm",".mov",".m4v") }).Count -gt 0
        }

    if ($igDirs.Count -eq 0) {
        Write-Host "  [SKIP] Aucun dossier IG_* avec videos" -ForegroundColor Yellow
        return
    }

    Write-Host "  $($igDirs.Count) dossier(s) carrousel avec video(s)" -ForegroundColor White
    Write-Host ""

    foreach ($igDir in $igDirs) {
        $outDir = Join-Path $CarouselTxDir $igDir.Name
        $logSubDir = Join-Path $LogDir ("carousels\" + $igDir.Name)

        Write-Host "  --- $($igDir.Name) ---" -ForegroundColor DarkCyan

        $pass2Args = @(
            "-SrcDir", $igDir.FullName,
            "-OutRoot", $outDir,
            "-LogRoot", $logSubDir,
            "-NoHeader"
        ) + $commonArgs

        & $WhisperEngine @pass2Args
    }

    Write-Host ""
    Write-Host "  Passe 2 terminee." -ForegroundColor Green
    Write-Host ""
}

# =============================================================================
# EXECUTION
# =============================================================================

$startTime = Get-Date

Invoke-Pass1
Invoke-Pass2

$elapsed = (Get-Date) - $startTime

Write-Host ""
Write-Host "  ============================================" -ForegroundColor Green
Write-Host "  ARSENAL TRANSCRIPTION TERMINEE" -ForegroundColor Green
Write-Host "  Duree totale : $([math]::Round($elapsed.TotalMinutes, 1)) minutes" -ForegroundColor White
Write-Host "  ============================================" -ForegroundColor Green
Write-Host ""

exit 0
