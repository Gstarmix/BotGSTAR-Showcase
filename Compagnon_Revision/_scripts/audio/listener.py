"""
listener.py — Push-to-talk global, capture micro vers WAV.

Hooke la touche ``ESPACE`` au niveau OS (global, fonctionne même si la
fenêtre Flask n'a pas le focus) via la lib ``keyboard``. Pendant que la
touche est maintenue, on enregistre le micro à 16 kHz mono via
``sounddevice``. Au relâchement, on sauvegarde le WAV dans
``_logs/audio/<timestamp>.wav`` et on déclenche le callback fourni.

Le WAV est encodé en PCM 16 bits via la stdlib ``wave`` (pas besoin de la
lib ``soundfile``). Format compatible avec ``faster_whisper`` qui re-resample
si besoin (mais 16 kHz mono est déjà son entrée native).

Cf. ARCHITECTURE.md §7.1.
"""

import logging
import threading
import wave
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import keyboard
import numpy as np
import sounddevice as sd

from config import AUDIO_LOGS_DIR

logger = logging.getLogger(__name__)


class PushToTalkListener:
    """Push-to-talk global hooké sur la touche ``ESPACE``.

    Usage type :

        def on_done(wav_path: Path) -> None:
            text, dur = transcriber.transcribe(wav_path)
            ...

        listener = PushToTalkListener(on_recording_complete=on_done)
        listener.start()  # main reste libre, hooks tournent en background
        ...
        listener.stop()   # nettoyage propre

    Le listener n'est pas réentrant : un appui sur ESPACE pendant un autre
    enregistrement en cours est ignoré (avec log debug).
    """

    SAMPLE_RATE = 16000     # natif Whisper
    CHANNELS = 1            # mono
    HOTKEY = "space"
    DTYPE = "float32"       # sounddevice par défaut, simple à convertir int16

    def __init__(
        self,
        on_recording_complete: Callable[[Path], None],
        output_dir: Optional[Path] = None,
    ):
        self._on_complete = on_recording_complete
        self._output_dir = Path(output_dir) if output_dir else AUDIO_LOGS_DIR
        self._frames: list[np.ndarray] = []
        self._stream: Optional[sd.InputStream] = None
        self._is_recording = False
        self._lock = threading.Lock()
        self._press_handle = None
        self._release_handle = None

    # ---------------------------------------------------------------- API

    def start(self) -> None:
        """Hooke ESPACE. Idempotent — un second appel sans stop() est ignoré."""
        if self._press_handle is not None:
            logger.debug("PushToTalkListener.start() ignore : deja hooke")
            return
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._press_handle = keyboard.on_press_key(self.HOTKEY, self._on_press)
        self._release_handle = keyboard.on_release_key(self.HOTKEY, self._on_release)
        logger.info("Push-to-talk arme sur la touche %r", self.HOTKEY)

    def stop(self) -> None:
        """Décroche les hooks et coupe un stream audio en cours si présent."""
        if self._press_handle is not None:
            keyboard.unhook(self._press_handle)
            self._press_handle = None
        if self._release_handle is not None:
            keyboard.unhook(self._release_handle)
            self._release_handle = None
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception as e:
                    logger.warning("Erreur fermeture stream a stop() : %s", e)
                self._stream = None
            self._is_recording = False
            self._frames = []

    # ---------------------------------------------------------------- callbacks keyboard

    def _on_press(self, _event) -> None:
        with self._lock:
            if self._is_recording:
                return
            self._is_recording = True
            self._frames = []
            try:
                self._stream = sd.InputStream(
                    samplerate=self.SAMPLE_RATE,
                    channels=self.CHANNELS,
                    dtype=self.DTYPE,
                    callback=self._audio_callback,
                )
                self._stream.start()
            except Exception as e:
                logger.error("Impossible de demarrer l'enregistrement : %s", e)
                self._is_recording = False
                self._stream = None

    def _on_release(self, _event) -> None:
        with self._lock:
            if not self._is_recording:
                return
            self._is_recording = False
            stream = self._stream
            self._stream = None
            frames_snapshot = self._frames
            self._frames = []

        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as e:
                logger.warning("Erreur fermeture stream : %s", e)

        if not frames_snapshot:
            logger.warning("Relachement sans frames audio capturees, on ignore")
            return

        try:
            wav_path = self._save_wav(frames_snapshot)
        except Exception as e:
            logger.error("Echec sauvegarde WAV : %s", e)
            return

        try:
            self._on_complete(wav_path)
        except Exception as e:
            logger.exception("Callback on_recording_complete a leve : %s", e)

    # ---------------------------------------------------------------- audio

    def _audio_callback(self, indata, _frames, _time, status) -> None:
        if status:
            logger.warning("Audio status: %s", status)
        # indata est un np.ndarray (frames, channels). Copy car réutilisé.
        self._frames.append(indata.copy())

    def _save_wav(self, frames: list[np.ndarray]) -> Path:
        """Concatène les frames float32 et écrit un WAV PCM16 16 kHz mono."""
        audio = np.concatenate(frames, axis=0).reshape(-1)  # (N,) mono
        # float32 [-1,1] -> int16 [-32768, 32767], avec clip pour saturation propre
        audio_int16 = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        wav_path = self._output_dir / f"{ts}.wav"
        # Si collision (deux enregistrements dans la meme seconde), on suffixe.
        if wav_path.exists():
            n = 1
            while True:
                candidate = self._output_dir / f"{ts}_{n}.wav"
                if not candidate.exists():
                    wav_path = candidate
                    break
                n += 1
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(self.CHANNELS)
            wf.setsampwidth(2)  # PCM 16 bits
            wf.setframerate(self.SAMPLE_RATE)
            wf.writeframes(audio_int16.tobytes())
        logger.info("WAV sauve : %s (%.2fs)", wav_path, len(audio) / self.SAMPLE_RATE)
        return wav_path
