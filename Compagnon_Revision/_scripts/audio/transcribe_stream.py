"""
transcribe_stream.py — Wrapper léger autour de faster-whisper.

Phase A : transcription **non-streaming** — le WAV complet est passé au
modèle qui retourne le texte intégral. Le streaming Whisper viendra en
Phase B si la latence du non-streaming s'avère gênante en pratique
(à mesurer d'abord).

Modèle par défaut : ``large-v3`` sur CUDA en ``int8_float16``. Tient dans
les 6 Go VRAM de la RTX 2060 et donne une qualité FR excellente.
Cf. pattern d'Arsenal_Arguments/whisper_engine.ps1.

Cf. ARCHITECTURE.md §7.2.
"""

import logging
from pathlib import Path

from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)


class WhisperTranscriber:
    """Wrapper non-streaming autour de ``faster_whisper.WhisperModel``.

    Le modèle est chargé en VRAM dans le constructeur — coût ~3 Go + quelques
    secondes au premier appel (ou plus selon la taille). Une instance est
    censée vivre toute la session : NE PAS réinstancier par WAV.
    """

    DEFAULT_MODEL_SIZE = "large-v3"
    DEFAULT_DEVICE = "cuda"
    DEFAULT_COMPUTE_TYPE = "int8_float16"
    DEFAULT_LANGUAGE = "fr"
    DEFAULT_VAD_MIN_SILENCE_MS = 500

    def __init__(
        self,
        model_size: str = DEFAULT_MODEL_SIZE,
        device: str = DEFAULT_DEVICE,
        compute_type: str = DEFAULT_COMPUTE_TYPE,
    ):
        logger.info(
            "Chargement Whisper %s (device=%s, compute=%s)...",
            model_size, device, compute_type,
        )
        self._model = WhisperModel(
            model_size, device=device, compute_type=compute_type
        )
        logger.info("Whisper pret.")

    def transcribe(
        self,
        wav_path: Path,
        language: str = DEFAULT_LANGUAGE,
    ) -> tuple[str, float]:
        """Transcrit ``wav_path`` et retourne ``(texte, duree_audio_secondes)``.

        VAD activé avec un seuil de silence de 500 ms — coupe les blancs trop
        longs en début/fin/inter-mots, améliore la qualité du joining. Le
        texte retourné est la concaténation des segments séparés par un
        espace, après ``strip()`` de chacun.
        """
        segments, info = self._model.transcribe(
            str(wav_path),
            language=language,
            vad_filter=True,
            vad_parameters={
                "min_silence_duration_ms": self.DEFAULT_VAD_MIN_SILENCE_MS,
            },
        )
        text = " ".join(seg.text.strip() for seg in segments)
        return text, info.duration
