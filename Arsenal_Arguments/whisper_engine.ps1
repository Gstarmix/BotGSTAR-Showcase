# =============================================================================
# batch_transcribe_ultimate.ps1 - Script unifie (Transcription + Watch)
# Version: 8.0 "Ultimate RTX 2060"
#
# PREREQUIS :
#   pip install faster-whisper nvidia-cublas-cu12 nvidia-cudnn-cu12
#
# UTILISATION :
#   .\batch_transcribe_ultimate.ps1                    # Mode normal
#   .\batch_transcribe_ultimate.ps1 -Watch             # Mode surveillance
#   .\batch_transcribe_ultimate.ps1 -Force             # Re-transcrire tout
#   .\batch_transcribe_ultimate.ps1 -Device cpu        # Forcer CPU
# =============================================================================

[CmdletBinding()]
param(
    [string]$SrcDir  = "C:\Users\Gstar\OneDrive\Documents\L1 Istic",
    [string]$OutRoot = "C:\Users\Gstar\Transcriptions\L1 Istic",
    [string]$LogRoot = "C:\Users\Gstar\Transcriptions\logs\L1 Istic",

    [ValidateSet("tiny","base","small","medium","large-v2","large-v3")]
    [string]$Model = "large-v3",
    [string]$Lang = "fr",
    [ValidateSet("transcribe","translate")]
    [string]$Task = "transcribe",
    [ValidateSet("txt","vtt","srt","tsv","json","all")]
    [string]$Fmt = "txt",

    [ValidateSet("auto","cuda","cpu")]
    [string]$Device = "auto",
    [ValidateSet("auto","float16","int8_float16","int8","float32")]
    [string]$ComputeType = "auto",

    [int]$BeamSize       = 5,
    [double]$Temperature = 0.0,
    [int]$BestOf         = 5,
    [switch]$NoVAD,
    [double]$VadThreshold = 0.35,

    [switch]$Watch,
    [int]$PollSeconds      = 15,
    [int]$MinStableSeconds = 20,
    [switch]$ShowQueue,

    [switch]$Force,
    [switch]$NoHeader,
    [int]$PerFileTimeoutSec = 3600,
    [int]$PauseSeconds      = 2
)

# =============================================================================
# INITIALISATION
# =============================================================================
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONWARNINGS    = "ignore"
$env:TQDM_MININTERVAL  = "1"

$AudioExts = @(".mp3",".wav",".m4a",".mp4",".mkv",".ogg",".flac",".webm",".aac",
               ".mov",".m4v",".avi",".wma",".opus")

# Compteurs globaux pour la session
$script:sessionStats = @{
    TotalFiles    = 0
    Transcribed   = 0
    Skipped       = 0
    Failed        = 0
    TotalAudioSec = 0.0
    TotalTimeSec  = 0.0
    StartTime     = $null
    Results       = [System.Collections.ArrayList]::new()
}

# =============================================================================
# FONCTIONS UI
# =============================================================================
function Write-Color {
    param([string]$Text, [ConsoleColor]$Color = "White", [switch]$NoNewline)
    if ($NoNewline) {
        Write-Host $Text -ForegroundColor $Color -NoNewline
    } else {
        Write-Host $Text -ForegroundColor $Color
    }
}

function Write-Banner {
    $banner = @"

    __        ___     _                       _   _ _ _   _                 _
    \ \      / / |__ (_)___ _ __   ___ _ __  | | | | | |_(_)_ __ ___   __ _| |_ ___
     \ \ /\ / /| '_ \| / __| '_ \ / _ \ '__| | | | | | __| | '_ ``_ \ / _`` | __/ _ \
      \ V  V / | | | | \__ \ |_) |  __/ |    | |_| | | |_| | | | | | | (_| | ||  __/
       \_/\_/  |_| |_|_|___/ .__/ \___|_|     \___/|_|\__|_|_| |_| |_|\__,_|\__\___|
                            |_|                                           v8.0
"@
    Write-Color $banner Cyan
}

function Write-Box {
    param([string]$Title, [hashtable[]]$Lines, [ConsoleColor]$BorderColor = "DarkGray", [ConsoleColor]$TitleColor = "Yellow")

    $maxKeyLen = ($Lines | ForEach-Object { $_.Key.Length } | Measure-Object -Maximum).Maximum
    $maxValLen = ($Lines | ForEach-Object { $_.Value.Length } | Measure-Object -Maximum).Maximum
    $innerWidth = [math]::Max($Title.Length + 4, $maxKeyLen + $maxValLen + 5)
    $totalWidth = $innerWidth + 2

    $topLine    = [char]0x2554 + ([string]([char]0x2550) * $totalWidth) + [char]0x2557
    $midLine    = [char]0x2560 + ([string]([char]0x2550) * $totalWidth) + [char]0x2563
    $bottomLine = [char]0x255A + ([string]([char]0x2550) * $totalWidth) + [char]0x255D
    $side       = [char]0x2551

    Write-Color $topLine $BorderColor
    # Title centered
    $titlePad = $totalWidth - $Title.Length
    $leftPad  = [math]::Floor($titlePad / 2)
    $rightPad = $titlePad - $leftPad
    Write-Color -Text $side -Color $BorderColor -NoNewline
    Write-Color -Text (" " * $leftPad) -Color $BorderColor -NoNewline
    Write-Color -Text $Title -Color $TitleColor -NoNewline
    Write-Color -Text (" " * $rightPad) -Color $BorderColor -NoNewline
    Write-Color $side $BorderColor
    Write-Color $midLine $BorderColor

    foreach ($line in $Lines) {
        $keyStr = $line.Key.PadRight($maxKeyLen)
        $valStr = $line.Value
        $padding = $innerWidth - $maxKeyLen - $valStr.Length - 3
        if ($padding -lt 0) { $padding = 0 }

        Write-Color -Text $side -Color $BorderColor -NoNewline
        Write-Host -NoNewline " "
        Write-Color -Text $keyStr -Color DarkCyan -NoNewline
        Write-Host -NoNewline " : "
        $valColor = if ($line.Color) { $line.Color } else { "White" }
        Write-Color -Text $valStr -Color $valColor -NoNewline
        Write-Host -NoNewline (" " * $padding)
        Write-Host -NoNewline " "
        Write-Color $side $BorderColor
    }

    Write-Color $bottomLine $BorderColor
}

function Write-FileProgress {
    param(
        [int]$Current,
        [int]$Total,
        [string]$FileName,
        [string]$Status,
        [ConsoleColor]$StatusColor = "White"
    )
    $pct = if ($Total -gt 0) { [math]::Round($Current / $Total * 100) } else { 0 }
    $barWidth = 30
    $filled = [math]::Round($barWidth * $Current / [math]::Max($Total, 1))
    $empty  = $barWidth - $filled
    $bar    = ([string]([char]0x2588) * $filled) + ([string]([char]0x2591) * $empty)

    Write-Host ""
    Write-Color -Text "  $bar " -Color DarkCyan -NoNewline
    Write-Color -Text "$pct% " -Color Cyan -NoNewline
    Write-Color -Text "($Current/$Total) " -Color DarkGray -NoNewline
    Write-Color $FileName White

    Write-Color -Text "  Status: " -Color DarkGray -NoNewline
    Write-Color $Status $StatusColor
}

function Write-TranscriptionProgress {
    param([int]$Pct, [string]$TimeInfo, [string]$Speed)

    $barWidth = 40
    $filled = [math]::Round($barWidth * $Pct / 100)
    $empty  = $barWidth - $filled
    $bar    = ([string]([char]0x2588) * $filled) + ([string]([char]0x2591) * $empty)

    # Use Write-Progress for smooth inline update
    $statusMsg = "$Pct% $TimeInfo"
    if ($Speed) { $statusMsg += " | $Speed" }
    Write-Progress -Activity "Transcription en cours" -Status $statusMsg -PercentComplete $Pct
}

function Write-FileResult {
    param(
        [string]$FileName,
        [string]$Status,      # OK, SKIP, FAIL
        [double]$AudioDuration,
        [double]$TranscribeTime,
        [double]$Ratio,
        [string]$ExtraInfo
    )

    switch ($Status) {
        "OK" {
            $icon  = [char]0x2714  # checkmark
            $color = "Green"
        }
        "SKIP" {
            $icon  = [char]0x25CB  # circle
            $color = "DarkGray"
        }
        "FAIL" {
            $icon  = [char]0x2718  # X
            $color = "Red"
        }
    }

    Write-Color -Text "  $icon " -Color $color -NoNewline
    Write-Color -Text $FileName.PadRight(35) -Color $color -NoNewline

    if ($Status -eq "OK") {
        $audioDurStr = Format-Duration $AudioDuration
        $transStr    = Format-Duration $TranscribeTime
        Write-Color -Text (" " + $audioDurStr + " audio") -Color DarkGray -NoNewline
        Write-Color -Text (" | " + $transStr) -Color DarkGray -NoNewline
        Write-Color -Text (" | x" + [math]::Round($Ratio, 1)) -Color Cyan -NoNewline
        if ($ExtraInfo) {
            Write-Color -Text (" | " + $ExtraInfo) -Color DarkGray
        } else {
            Write-Host ""
        }
    } elseif ($Status -eq "SKIP") {
        Write-Color " (deja transcrit)" DarkGray
    } else {
        Write-Color (" ECHEC: " + $ExtraInfo) Red
    }
}

function Format-Duration {
    param([double]$Seconds)
    if ($Seconds -lt 60) {
        return [math]::Round($Seconds, 0).ToString() + "s"
    }
    $m = [math]::Floor($Seconds / 60)
    $s = [math]::Round($Seconds % 60)
    if ($m -lt 60) {
        return $m.ToString() + "m" + $s.ToString("00") + "s"
    }
    $h = [math]::Floor($m / 60)
    $m = $m % 60
    return $h.ToString() + "h" + $m.ToString("00") + "m"
}

function Write-SummaryTable {
    $stats = $script:sessionStats
    $elapsed = if ($stats.StartTime) { ((Get-Date) - $stats.StartTime).TotalSeconds } else { 0 }

    Write-Host ""
    Write-Host ""

    $summaryLines = @(
        @{ Key = "Fichiers traites";    Value = $stats.Transcribed.ToString();    Color = "Green" }
        @{ Key = "Deja presents";       Value = $stats.Skipped.ToString();        Color = "DarkGray" }
        @{ Key = "Echecs";              Value = $stats.Failed.ToString();         Color = $(if ($stats.Failed -gt 0) { "Red" } else { "Green" }) }
        @{ Key = "Audio total";         Value = Format-Duration $stats.TotalAudioSec; Color = "White" }
        @{ Key = "Temps transcription"; Value = Format-Duration $stats.TotalTimeSec;  Color = "White" }
        @{ Key = "Temps session";       Value = Format-Duration $elapsed;            Color = "White" }
    )

    if ($stats.TotalTimeSec -gt 0) {
        $avgRatio = $stats.TotalAudioSec / $stats.TotalTimeSec
        $summaryLines += @{ Key = "Ratio moyen"; Value = ("x" + [math]::Round($avgRatio, 1) + " temps reel"); Color = "Cyan" }
    }

    Write-Box -Title "RESUME DE SESSION" -Lines $summaryLines -TitleColor Green

    # Afficher le detail par fichier si des transcriptions ont eu lieu
    if ($stats.Results.Count -gt 0) {
        Write-Host ""
        Write-Color "  Fichiers transcrits:" Cyan
        Write-Color ("  " + ("-" * 70)) DarkGray
        foreach ($r in $stats.Results) {
            if ($r.Status -eq "OK") {
                $audioStr = (Format-Duration $r.AudioDuration).PadRight(8)
                $timeStr  = (Format-Duration $r.TranscribeTime).PadRight(8)
                $ratioStr = ("x" + [math]::Round($r.Ratio, 1)).PadRight(6)
                Write-Color -Text ("  " + [char]0x2714 + " ") -Color Green -NoNewline
                Write-Color -Text $r.Name.PadRight(30) -Color White -NoNewline
                Write-Color -Text (" " + $audioStr) -Color DarkGray -NoNewline
                Write-Color -Text (" " + $timeStr) -Color DarkGray -NoNewline
                Write-Color (" " + $ratioStr) Cyan
            }
        }
        Write-Color ("  " + ("-" * 70)) DarkGray
    }

    Write-Host ""
    Write-Color ("  Transcriptions : " + $OutRoot) DarkGray
    Write-Color ("  Logs           : " + $LogRoot) DarkGray
    Write-Host ""
}

# =============================================================================
# FONCTIONS UTILITAIRES
# =============================================================================
function Ensure-Dir([string]$p) {
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }
}

function Get-RelPath([string]$Base, [string]$Full) {
    if ($Full.StartsWith($Base, [System.StringComparison]::InvariantCultureIgnoreCase)) {
        return $Full.Substring($Base.Length).TrimStart('\')
    }
    return ""
}

# =============================================================================
# DETECTION AUTO GPU / COMPUTE TYPE
# =============================================================================
function Resolve-DeviceAndCompute {
    $selectedDevice = $Device
    $selectedCompute = $ComputeType

    if ($selectedDevice -eq "auto") {
        $cudaTest = & python -c "import ctranslate2; print('cuda' if ctranslate2.get_cuda_device_count() > 0 else 'cpu')" 2>$null
        if ($cudaTest -eq "cuda") {
            $selectedDevice = "cuda"
        } else {
            $cudaTest2 = & python -c "import torch; print('cuda' if torch.cuda.is_available() else 'cpu')" 2>$null
            if ($cudaTest2 -eq "cuda") {
                $selectedDevice = "cuda"
            } else {
                $selectedDevice = "cpu"
            }
        }
    }

    if ($selectedCompute -eq "auto") {
        if ($selectedDevice -eq "cuda") {
            $selectedCompute = "int8_float16"
        } else {
            $selectedCompute = "int8"
        }
    }

    return @{ Device = $selectedDevice; Compute = $selectedCompute }
}

# =============================================================================
# SCRIPT PYTHON (avec progress file pour la barre temps reel)
# =============================================================================
function Get-PythonTranscribeScript {
    return @'
import sys, os, json, time, traceback, glob

def main():
    try:
        _setup_nvidia_dlls()
        _run()
    except Exception:
        traceback.print_exc()
        sys.exit(1)

def _setup_nvidia_dlls():
    site_packages = None
    for p in sys.path:
        if "site-packages" in p and os.path.isdir(p):
            site_packages = p
            break
    if not site_packages:
        return
    nvidia_dir = os.path.join(site_packages, "nvidia")
    if not os.path.isdir(nvidia_dir):
        return
    dll_dirs = []
    for root, dirs, files in os.walk(nvidia_dir):
        basename = os.path.basename(root)
        if basename in ("bin", "lib"):
            dll_dirs.append(root)
    if dll_dirs:
        current_path = os.environ.get("PATH", "")
        new_dirs = [d for d in dll_dirs if d not in current_path]
        if new_dirs:
            os.environ["PATH"] = ";".join(new_dirs) + ";" + current_path
        if hasattr(os, "add_dll_directory"):
            for d in dll_dirs:
                try:
                    os.add_dll_directory(d)
                except OSError:
                    pass

def _run():
    with open(sys.argv[1], "r", encoding="utf-8-sig") as f:
        args = json.load(f)

    input_file    = args["input"]
    output_dir    = args["output_dir"]
    model_size    = args["model"]
    device        = args["device"]
    compute_type  = args["compute_type"]
    language      = args["language"]
    task          = args["task"]
    fmt           = args["fmt"]
    beam_size     = args["beam_size"]
    temperature   = args["temperature"]
    best_of       = args["best_of"]
    use_vad       = args["use_vad"]
    vad_threshold = args["vad_threshold"]
    progress_file = args.get("progress_file", "")

    def update_progress(pct, audio_pos, audio_total, phase="transcribing"):
        if not progress_file:
            return
        try:
            data = {
                "pct": pct,
                "audio_pos": round(audio_pos, 1),
                "audio_total": round(audio_total, 1),
                "phase": phase,
                "timestamp": time.time()
            }
            tmp = progress_file + ".tmp"
            with open(tmp, "w") as pf:
                json.dump(data, pf)
            # Atomic rename
            if os.path.exists(progress_file):
                os.remove(progress_file)
            os.rename(tmp, progress_file)
        except:
            pass

    update_progress(0, 0, 0, "loading_model")

    t0 = time.time()
    from faster_whisper import WhisperModel

    vad_params = None
    if use_vad:
        vad_params = {
            "threshold": vad_threshold,
            "min_speech_duration_ms": 250,
            "min_silence_duration_ms": 1000,
            "speech_pad_ms": 250,
        }

    model = WhisperModel(
        model_size,
        device=device,
        compute_type=compute_type,
        cpu_threads=os.cpu_count() if device == "cpu" else 4,
        num_workers=1
    )
    t_load = time.time() - t0

    update_progress(0, 0, 0, "transcribing")
    t1 = time.time()

    segments, info = model.transcribe(
        input_file,
        language=language,
        task=task,
        beam_size=beam_size,
        temperature=temperature if temperature > 0 else 0.0,
        best_of=best_of if temperature > 0 else 1,
        vad_filter=use_vad,
        vad_parameters=vad_params if use_vad else None,
        condition_on_previous_text=False,
        no_speech_threshold=0.6,
        log_prob_threshold=-1.0,
        compression_ratio_threshold=2.4,
        word_timestamps=False,
    )

    all_segments = []
    for seg in segments:
        all_segments.append(seg)
        if info.duration and info.duration > 0:
            pct = min(100, int(seg.end / info.duration * 100))
            update_progress(pct, seg.end, info.duration, "transcribing")

    t_transcribe = time.time() - t1
    duration = info.duration or 0
    ratio = duration / t_transcribe if t_transcribe > 0 else 0

    update_progress(100, duration, duration, "writing")

    # Ecriture des fichiers
    basename = os.path.splitext(os.path.basename(input_file))[0]
    os.makedirs(output_dir, exist_ok=True)
    formats = [fmt] if fmt != "all" else ["txt", "vtt", "srt", "tsv", "json"]

    for f in formats:
        outpath = os.path.join(output_dir, f"{basename}.{f}")
        if f == "txt":
            with open(outpath, "w", encoding="utf-8") as fh:
                for seg in all_segments:
                    fh.write(seg.text.strip() + "\n")
        elif f == "srt":
            with open(outpath, "w", encoding="utf-8") as fh:
                for i, seg in enumerate(all_segments, 1):
                    fh.write(f"{i}\n{fmt_srt(seg.start)} --> {fmt_srt(seg.end)}\n{seg.text.strip()}\n\n")
        elif f == "vtt":
            with open(outpath, "w", encoding="utf-8") as fh:
                fh.write("WEBVTT\n\n")
                for seg in all_segments:
                    fh.write(f"{fmt_vtt(seg.start)} --> {fmt_vtt(seg.end)}\n{seg.text.strip()}\n\n")
        elif f == "tsv":
            with open(outpath, "w", encoding="utf-8") as fh:
                fh.write("start\tend\ttext\n")
                for seg in all_segments:
                    fh.write(f"{int(seg.start*1000)}\t{int(seg.end*1000)}\t{seg.text.strip()}\n")
        elif f == "json":
            import json as jm
            data = {"language": info.language, "duration": duration,
                    "segments": [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in all_segments]}
            with open(outpath, "w", encoding="utf-8") as fh:
                jm.dump(data, fh, ensure_ascii=False, indent=2)

    # Ecrire le resultat final dans le progress file
    update_progress(100, duration, duration, "done")

    # Sortie JSON finale sur stdout pour PowerShell
    result = {
        "status": "ok",
        "audio_duration": round(duration, 1),
        "transcribe_time": round(t_transcribe, 1),
        "model_load_time": round(t_load, 1),
        "ratio": round(ratio, 1),
        "language": info.language,
        "language_prob": round(info.language_probability, 2),
        "segments_count": len(all_segments),
        "output_files": formats
    }
    print("RESULT_JSON:" + json.dumps(result))

def fmt_srt(s):
    h=int(s//3600); m=int((s%3600)//60); sec=int(s%60); ms=int((s-int(s))*1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"

def fmt_vtt(s):
    h=int(s//3600); m=int((s%3600)//60); sec=int(s%60); ms=int((s-int(s))*1000)
    return f"{h:02d}:{m:02d}:{sec:02d}.{ms:03d}"

if __name__ == "__main__":
    main()
'@
}

# =============================================================================
# TRANSCRIPTION AVEC PROGRESS BAR TEMPS REEL
# =============================================================================
function Invoke-Transcribe {
    param(
        [string]$InputFile,
        [string]$OutputDir,
        [string]$LogFile,
        [hashtable]$DeviceConfig
    )

    $pyScript = Get-PythonTranscribeScript
    $pyScriptPath = Join-Path $env:TEMP "whisper_transcribe_ultimate.py"
    Set-Content -Path $pyScriptPath -Value $pyScript -Encoding UTF8

    # Progress file for real-time bar
    $progressFile = Join-Path $env:TEMP ("whisper_progress_" + [guid]::NewGuid().ToString('N') + ".json")

    $argsObj = @{
        input         = $InputFile
        output_dir    = $OutputDir
        model         = $Model
        device        = $DeviceConfig.Device
        compute_type  = $DeviceConfig.Compute
        language      = $Lang
        task          = $Task
        fmt           = $Fmt
        beam_size     = $BeamSize
        temperature   = $Temperature
        best_of       = $BestOf
        use_vad       = (-not $NoVAD)
        vad_threshold = $VadThreshold
        progress_file = $progressFile
    }
    $argsJson = $argsObj | ConvertTo-Json -Compress
    $argsFile = Join-Path $env:TEMP ("whisper_args_" + [guid]::NewGuid().ToString('N') + ".json")
    [System.IO.File]::WriteAllText($argsFile, $argsJson, (New-Object System.Text.UTF8Encoding $false))

    Ensure-Dir $OutputDir
    New-Item -ItemType File -Path $LogFile -Force | Out-Null

    $sw = [System.Diagnostics.Stopwatch]::StartNew()

    # Lancer Python en arriere-plan
    $stdoutFile = Join-Path $env:TEMP ("whisper_stdout_" + [guid]::NewGuid().ToString('N') + ".txt")
    $stderrFile = Join-Path $env:TEMP ("whisper_stderr_" + [guid]::NewGuid().ToString('N') + ".txt")

    $proc = Start-Process -FilePath "python" `
        -ArgumentList ('"' + $pyScriptPath + '" "' + $argsFile + '"') `
        -NoNewWindow -PassThru `
        -RedirectStandardOutput $stdoutFile `
        -RedirectStandardError $stderrFile

    # Boucle de mise a jour du progress bar
    $fileName = [System.IO.Path]::GetFileName($InputFile)
    $lastPct = -1

    while (-not $proc.HasExited) {
        Start-Sleep -Milliseconds 500

        if (Test-Path $progressFile) {
            try {
                $pData = Get-Content -Path $progressFile -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json -ErrorAction SilentlyContinue
                if ($pData) {
                    $pct = [int]$pData.pct
                    $audioPos = [double]$pData.audio_pos
                    $audioTotal = [double]$pData.audio_total
                    $phase = $pData.phase

                    if ($pct -ne $lastPct -or $lastPct -eq -1) {
                        $lastPct = $pct

                        switch ($phase) {
                            "loading_model" {
                                Write-Progress -Id 1 -Activity ("Transcription: " + $fileName) `
                                    -Status "Chargement du modele..." -PercentComplete 0
                            }
                            "transcribing" {
                                $posStr = Format-Duration $audioPos
                                $totStr = Format-Duration $audioTotal
                                $elapsedSec = $sw.Elapsed.TotalSeconds
                                $eta = ""
                                if ($pct -gt 2 -and $elapsedSec -gt 5) {
                                    $estimatedTotal = $elapsedSec / ($pct / 100.0)
                                    $remaining = $estimatedTotal - $elapsedSec
                                    if ($remaining -gt 0) {
                                        $eta = " | ETA: " + (Format-Duration $remaining)
                                    }
                                }
                                $speedRatio = ""
                                if ($elapsedSec -gt 2 -and $audioPos -gt 0) {
                                    $currentRatio = [math]::Round($audioPos / $elapsedSec, 1)
                                    $speedRatio = " | x" + $currentRatio + " temps reel"
                                }

                                Write-Progress -Id 1 -Activity ("Transcription: " + $fileName) `
                                    -Status ($pct.ToString() + "% (" + $posStr + "/" + $totStr + ")" + $speedRatio + $eta) `
                                    -PercentComplete ([math]::Min($pct, 100))
                            }
                            "writing" {
                                Write-Progress -Id 1 -Activity ("Transcription: " + $fileName) `
                                    -Status "Ecriture des fichiers..." -PercentComplete 100
                            }
                            "done" {
                                Write-Progress -Id 1 -Activity ("Transcription: " + $fileName) `
                                    -Completed
                            }
                        }
                    }
                }
            } catch {
                # Fichier en cours d'ecriture, ignorer
            }
        }
    }

    # Fermer la progress bar
    Write-Progress -Id 1 -Activity "Transcription" -Completed

    $sw.Stop()

    # Lire les sorties
    $result = $null
    $stdout = ""
    $stderr = ""

    if (Test-Path $stdoutFile) {
        $stdout = Get-Content -Path $stdoutFile -Raw -ErrorAction SilentlyContinue
        if ($stdout) {
            Add-Content -Path $LogFile -Value $stdout -Encoding UTF8
            # Parser le resultat JSON
            foreach ($line in ($stdout -split "`n")) {
                if ($line.StartsWith("RESULT_JSON:")) {
                    $jsonStr = $line.Substring("RESULT_JSON:".Length).Trim()
                    try { $result = $jsonStr | ConvertFrom-Json } catch {}
                }
            }
        }
    }

    if (Test-Path $stderrFile) {
        $stderr = Get-Content -Path $stderrFile -Raw -ErrorAction SilentlyContinue
        if ($stderr) {
            Add-Content -Path $LogFile -Value ("`nSTDERR:`n" + $stderr) -Encoding UTF8
            # Afficher les erreurs
            Write-Color ("  " + $stderr.Trim()) Red
        }
    }

    # Nettoyage
    Remove-Item -Path $argsFile -Force -ErrorAction SilentlyContinue
    Remove-Item -Path $stdoutFile -Force -ErrorAction SilentlyContinue
    Remove-Item -Path $stderrFile -Force -ErrorAction SilentlyContinue
    Remove-Item -Path $progressFile -Force -ErrorAction SilentlyContinue
    Remove-Item -Path ($progressFile + ".tmp") -Force -ErrorAction SilentlyContinue

    return @{
        Elapsed  = $sw.Elapsed
        ExitCode = $proc.ExitCode
        Result   = $result
    }
}

# =============================================================================
# AJOUT EN-TETE INTELLIGENT
# =============================================================================
function Add-TranscriptHeader {
    param(
        [string]$TxtPath,
        [string]$BaseName,
        [datetime]$FileCreationTime,
        $TranscribeResult
    )
    if ($NoHeader) { return }

    try {
        $rawContent = Get-Content -Path $TxtPath -Raw -Encoding UTF8
        $coursDate = "Non specifiee"

        if ($BaseName -match "(\d{2})(\d{2})(\d{4})$") {
            $coursDate = $matches[1] + "/" + $matches[2] + "/" + $matches[3]
        } elseif ($BaseName -match "(\d{2})(\d{2})(\d{2})$") {
            $coursDate = $matches[1] + "/" + $matches[2] + "/20" + $matches[3]
        } elseif ($BaseName -match "(\d{2})(\d{2})$") {
            $coursDate = $matches[1] + "/" + $matches[2] + "/" + $FileCreationTime.Year.ToString()
        } else {
            $coursDate = $FileCreationTime.ToString("dd/MM/yyyy")
        }

        $sep = "================================================================================"
        $nl = [Environment]::NewLine
        $header = $sep + $nl
        $header += "FICHIER SOURCE  : " + $BaseName + $nl
        $header += "DATE DU COURS   : " + $coursDate + $nl
        $header += "TRANSCRIPTION   : " + (Get-Date -Format 'dd/MM/yyyy HH:mm') + $nl
        $header += "MODELE          : " + $Model + " (" + $script:resolvedConfig.Compute + " / " + $script:resolvedConfig.Device + ")" + $nl
        if ($TranscribeResult -and $TranscribeResult.Result) {
            $r = $TranscribeResult.Result
            $header += "DUREE AUDIO     : " + (Format-Duration $r.audio_duration) + $nl
            $header += "TEMPS TRAITEMENT: " + (Format-Duration $r.transcribe_time) + " (x" + $r.ratio + " temps reel)" + $nl
            $header += "SEGMENTS        : " + $r.segments_count + $nl
        }
        $header += $sep + $nl + $nl

        Set-Content -Path $TxtPath -Value ($header + $rawContent) -Encoding UTF8
    } catch {
        Write-Color ("  Erreur en-tete: " + $_.Exception.Message) Yellow
    }
}

# =============================================================================
# COLLECTE DES FICHIERS
# =============================================================================
function Get-AudioFiles {
    if (-not (Test-Path $SrcDir)) {
        Write-Color ("Dossier source introuvable: " + $SrcDir) Red
        exit 1
    }
    return @(Get-ChildItem -Path $SrcDir -File -Recurse |
        Where-Object { $AudioExts -contains $_.Extension.ToLowerInvariant() })
}

# =============================================================================
# MODE WATCH - Stabilite des fichiers
# =============================================================================
$watchState = @{}

function Test-FileStable {
    param([System.IO.FileInfo]$File, [datetime]$Now)
    $key = $File.FullName.ToLowerInvariant()
    $curSize = $File.Length
    $entry = $watchState[$key]

    if ($null -eq $entry) {
        $watchState[$key] = @{ Size = $curSize; FirstSeen = $Now; LastWrite = $File.LastWriteTime }
        return $false
    }

    $sizeSame = ($entry.Size -eq $curSize)
    $ageSec = ($Now - $File.LastWriteTime).TotalSeconds

    if ($sizeSame -and ($ageSec -ge $MinStableSeconds)) { return $true }

    if (-not $sizeSame) {
        $watchState[$key] = @{ Size = $curSize; FirstSeen = $Now; LastWrite = $File.LastWriteTime }
    }
    return $false
}

# =============================================================================
# BOUCLE PRINCIPALE
# =============================================================================
function Invoke-TranscriptionPass {
    param([hashtable]$DeviceConfig)

    $files = Get-AudioFiles
    if ($files.Count -eq 0) {
        Write-Color "  Aucun fichier audio trouve." Yellow
        return
    }

    # Compter les fichiers a transcrire
    $toTranscribe = @()
    $toSkip = @()
    foreach ($f in $files) {
        $relDir = Get-RelPath -Base $SrcDir -Full $f.DirectoryName
        $outDir = if ([string]::IsNullOrEmpty($relDir)) { $OutRoot } else { Join-Path $OutRoot $relDir }
        $outTxt = Join-Path $outDir ($f.BaseName + ".txt")
        if ((-not $Force) -and (Test-Path $outTxt)) {
            $toSkip += $f
        } else {
            $toTranscribe += $f
        }
    }

    $script:sessionStats.TotalFiles = $files.Count

    # Afficher les skips de maniere compacte
    if ($toSkip.Count -gt 0) {
        Write-Host ""
        Write-Color ("  " + [char]0x25CB + " " + $toSkip.Count + " fichiers deja transcrits (skip)") DarkGray
        $script:sessionStats.Skipped += $toSkip.Count
    }

    if ($toTranscribe.Count -eq 0) {
        Write-Host ""
        Write-Color "  Tous les fichiers sont deja transcrits." Green
        return
    }

    Write-Host ""
    Write-Color ("  " + $toTranscribe.Count + " fichier(s) a transcrire:") Cyan
    Write-Color ("  " + ("-" * 70)) DarkGray

    $i = 0
    foreach ($f in $toTranscribe) {
        $i++
        $basename = $f.BaseName

        # En mode Watch, verifier stabilite
        if ($Watch) {
            $now = Get-Date
            if (-not (Test-FileStable -File $f -Now $now)) {
                Write-Color ("  " + [char]0x25CB + " " + $basename + " (en cours de copie, skip)") DarkGray
                continue
            }
        }

        $relDir = Get-RelPath -Base $SrcDir -Full $f.DirectoryName
        $outDir = if ([string]::IsNullOrEmpty($relDir)) { $OutRoot } else { Join-Path $OutRoot $relDir }
        $logDir = if ([string]::IsNullOrEmpty($relDir)) { $LogRoot } else { Join-Path $LogRoot $relDir }
        Ensure-Dir $outDir
        Ensure-Dir $logDir

        $outTxt = Join-Path $outDir ($basename + ".txt")
        $outLog = Join-Path $logDir ($basename + ".session.log")

        # Afficher le fichier en cours
        $sizeMB = [math]::Round($f.Length / 1MB, 1)
        Write-Host ""
        Write-FileProgress -Current $i -Total $toTranscribe.Count -FileName $f.Name `
            -Status ($sizeMB.ToString() + " Mo") -StatusColor DarkGray

        # Transcrire
        $trResult = Invoke-Transcribe `
            -InputFile $f.FullName `
            -OutputDir $outDir `
            -LogFile $outLog `
            -DeviceConfig $DeviceConfig

        if ((Test-Path $outTxt) -and $trResult.Result) {
            $r = $trResult.Result
            Add-TranscriptHeader -TxtPath $outTxt -BaseName $basename `
                -FileCreationTime $f.CreationTime -TranscribeResult $trResult

            Write-FileResult -FileName $basename -Status "OK" `
                -AudioDuration $r.audio_duration `
                -TranscribeTime $r.transcribe_time `
                -Ratio $r.ratio

            $script:sessionStats.Transcribed++
            $script:sessionStats.TotalAudioSec += $r.audio_duration
            $script:sessionStats.TotalTimeSec  += $r.transcribe_time
            [void]$script:sessionStats.Results.Add(@{
                Name = $basename; Status = "OK"
                AudioDuration = $r.audio_duration
                TranscribeTime = $r.transcribe_time
                Ratio = $r.ratio
            })
        } elseif (Test-Path $outTxt) {
            # Fichier cree mais pas de result JSON (vieille erreur?)
            Add-TranscriptHeader -TxtPath $outTxt -BaseName $basename `
                -FileCreationTime $f.CreationTime -TranscribeResult $null
            Write-FileResult -FileName $basename -Status "OK" -AudioDuration 0 -TranscribeTime $trResult.Elapsed.TotalSeconds -Ratio 0
            $script:sessionStats.Transcribed++
        } else {
            Write-FileResult -FileName $basename -Status "FAIL" -AudioDuration 0 -TranscribeTime 0 -Ratio 0 -ExtraInfo ("code " + $trResult.ExitCode + " | voir " + $outLog)
            $script:sessionStats.Failed++
            [void]$script:sessionStats.Results.Add(@{ Name = $basename; Status = "FAIL" })
        }

        Start-Sleep -Seconds $PauseSeconds
    }
}

# =============================================================================
# DEMARRAGE
# =============================================================================
Clear-Host
Write-Banner
$script:sessionStats.StartTime = Get-Date

# Resoudre device et compute type
$script:resolvedConfig = Resolve-DeviceAndCompute

# GPU info
$gpuInfo = "N/A"
$gpuMem  = "N/A"
if ($script:resolvedConfig.Device -eq "cuda") {
    try {
        $gpuInfo = (& python -c "import ctranslate2; print(ctranslate2.get_cuda_device_count())" 2>&1).ToString() + " GPU(s) CUDA"
    } catch {}
    try {
        $gpuInfo = & python -c "import torch; print(torch.cuda.get_device_name(0))" 2>&1
        $gpuMem  = (& python -c "import torch; print(f'{torch.cuda.get_device_properties(0).total_mem / 1024**3:.1f}')" 2>&1) + " Go"
    } catch {}
}

# Afficher la config dans une box
$configLines = @(
    @{ Key = "Source";      Value = $SrcDir; Color = "White" }
    @{ Key = "Sortie";      Value = $OutRoot; Color = "White" }
    @{ Key = "Modele";      Value = $Model; Color = "Cyan" }
    @{ Key = "Device";      Value = $script:resolvedConfig.Device.ToUpper(); Color = $(if ($script:resolvedConfig.Device -eq "cuda") { "Green" } else { "Yellow" }) }
    @{ Key = "Compute";     Value = $script:resolvedConfig.Compute; Color = "White" }
    @{ Key = "GPU";         Value = $gpuInfo; Color = "Cyan" }
    @{ Key = "VRAM";        Value = $gpuMem; Color = "Cyan" }
    @{ Key = "VAD";         Value = $(if ($NoVAD) { "OFF" } else { "ON (seuil=" + $VadThreshold + ")" }); Color = $(if ($NoVAD) { "Yellow" } else { "Green" }) }
    @{ Key = "Beam";        Value = $BeamSize.ToString(); Color = "White" }
    @{ Key = "Temperature"; Value = $Temperature.ToString(); Color = "White" }
    @{ Key = "Mode";        Value = $(if ($Watch) { "WATCH (poll=" + $PollSeconds + "s)" } else { "NORMAL" }); Color = $(if ($Watch) { "Yellow" } else { "Cyan" }) }
)

Write-Box -Title "CONFIGURATION" -Lines $configLines

# Verifier faster-whisper
Write-Host ""
try {
    $fwVer = & python -c "import faster_whisper; print(faster_whisper.__version__)" 2>&1
    Write-Color ("  " + [char]0x2714 + " faster-whisper v" + $fwVer) Green
} catch {
    Write-Color ("  " + [char]0x2718 + " faster-whisper non installe!") Red
    Write-Color "  pip install faster-whisper" Yellow
    exit 1
}

# Verifier ctranslate2 CUDA
if ($script:resolvedConfig.Device -eq "cuda") {
    try {
        $ct2gpu = & python -c "import ctranslate2; print(ctranslate2.get_cuda_device_count())" 2>&1
        if ([int]$ct2gpu -gt 0) {
            Write-Color ("  " + [char]0x2714 + " ctranslate2 CUDA OK (" + $ct2gpu + " GPU)") Green
        } else {
            Write-Color ("  " + [char]0x2718 + " ctranslate2 ne voit pas de GPU CUDA") Red
            $script:resolvedConfig.Device = "cpu"
            $script:resolvedConfig.Compute = "int8"
        }
    } catch {
        Write-Color ("  " + [char]0x2718 + " Erreur test ctranslate2 CUDA") Red
    }
}

Ensure-Dir $OutRoot
Ensure-Dir $LogRoot

if ($Watch) {
    Write-Host ""
    Write-Color "  Mode Watch active - Ctrl+C pour arreter" Yellow
    $cycle = 0
    while ($true) {
        $cycle++
        Write-Host ""
        Write-Color ("  --- Cycle " + $cycle + " | " + (Get-Date -Format 'HH:mm:ss') + " ---") DarkGray
        Invoke-TranscriptionPass -DeviceConfig $script:resolvedConfig
        Write-Color ("  Prochain scan dans " + $PollSeconds + "s...") DarkGray
        Start-Sleep -Seconds $PollSeconds
    }
} else {
    Invoke-TranscriptionPass -DeviceConfig $script:resolvedConfig
    Write-SummaryTable
}