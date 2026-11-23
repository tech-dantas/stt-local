"""Ditado em qualquer programa: aperte o atalho, fale, e o texto aparece onde o cursor estiver.

Funciona por fora dos programas (OpenCode no terminal, navegador, Word, chat...), sem integração:
- Atalho global lido do teclado (GetAsyncKeyState), sem gancho no sistema e sem "engolir" a tecla.
  Modo "segurar": grava enquanto a tecla está apertada. Modo "alternar": um toque começa, outro para.
- Microfone por sounddevice (PortAudio), 16 kHz mono.
- Texto digitado com SendInput em Unicode (acentos e emojis), sem mexer na área de transferência.
  Opcional: aperta Enter no fim, para enviar sozinho.

Limite do Windows: um programa aberto "como administrador" não aceita teclas de um programa comum.
"""
from __future__ import annotations

import ctypes
import io
import logging
import threading
import time
import wave
from ctypes import wintypes
from typing import Any, Callable

import numpy as np

log = logging.getLogger("stt")

SR = 16000
MIN_S = 0.35          # gravação mais curta que isso é ignorada (toque acidental)
MAX_S = 300           # uma fala de até 5 min por vez
TOQUE_MAX_S = 0.45    # modo alternar: tecla apertada e solta em até isso, sem outra tecla junto

TECLAS: dict[str, tuple[int, str]] = {
    "ctrl_direito": (0xA3, "Ctrl direito"),
    "alt_direito": (0xA5, "Alt direito (AltGr)"),
    "f8": (0x77, "F8"),
    "f9": (0x78, "F9"),
    "f10": (0x79, "F10"),
    "pause": (0x13, "Pause/Break"),
    "scroll_lock": (0x91, "Scroll Lock"),
}
PADRAO = {"ativo": False, "tecla": "ctrl_direito", "modo": "segurar", "enter": False, "som": True}

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_user32.GetAsyncKeyState.restype = ctypes.c_short

INPUT_KEYBOARD, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 1, 0x0002, 0x0004
VK_RETURN = 0x0D
ULONG_PTR = ctypes.c_size_t


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


_user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
_user32.SendInput.restype = wintypes.UINT


def _apertada(vk: int) -> bool:
    return bool(_user32.GetAsyncKeyState(vk) & 0x8000)


def _outra_tecla_apertada(exceto: int) -> bool:
    """Alguma tecla além do atalho (Shift/Ctrl/Alt genéricos espelham o próprio atalho e ficam de fora)."""
    ignorar = {exceto, 0x10, 0x11, 0x12}
    return any(_apertada(vk) for vk in range(0x08, 0xFF) if vk not in ignorar)


def digitar(texto: str, enter: bool = False) -> None:
    """Digita o texto na janela em foco, como se viesse do teclado."""
    eventos: list[_INPUT] = []
    dados = texto.encode("utf-16-le")
    for i in range(0, len(dados), 2):
        unidade = int.from_bytes(dados[i:i + 2], "little")
        for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
            ev = _INPUT(type=INPUT_KEYBOARD)
            ev.u.ki = _KEYBDINPUT(0, unidade, flags, 0, 0)
            eventos.append(ev)
    if enter:
        for flags in (0, KEYEVENTF_KEYUP):
            ev = _INPUT(type=INPUT_KEYBOARD)
            ev.u.ki = _KEYBDINPUT(VK_RETURN, 0, flags, 0, 0)
            eventos.append(ev)
    # Em lotes: alguns programas perdem teclas quando chegam milhares de uma vez.
    for i in range(0, len(eventos), 200):
        lote = eventos[i:i + 200]
        arr = (_INPUT * len(lote))(*lote)
        if _user32.SendInput(len(lote), arr, ctypes.sizeof(_INPUT)) != len(lote):
            raise OSError(ctypes.get_last_error(), "SendInput recusado (programa aberto como administrador?)")
        time.sleep(0.01)


def _wav(audio: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def _bip(som: bool, freq: int, ms: int) -> None:
    if som:
        import winsound
        threading.Thread(target=winsound.Beep, args=(freq, ms), daemon=True).start()


class Ditado:
    """Laço do atalho + gravação. `transcrever(wav_bytes) -> texto` vem do servidor."""

    def __init__(self, transcrever: Callable[[bytes], str]) -> None:
        self._transcrever = transcrever
        self.cfg: dict[str, Any] = dict(PADRAO)
        self.estado = "desligado"            # desligado | pronto | ouvindo | transcrevendo | erro
        self.erro: str | None = None
        self.ultimo: dict[str, Any] | None = None
        self.total = 0
        self._parar = threading.Event()
        self._thread: threading.Thread | None = None
        self._partes: list[np.ndarray] = []
        self._stream: Any = None
        self._inicio = 0.0

    def aplicar(self, cfg: dict[str, Any]) -> None:
        self.cfg = {**PADRAO, **{k: v for k, v in cfg.items() if k in PADRAO}}
        if self.cfg["tecla"] not in TECLAS:
            self.cfg["tecla"] = PADRAO["tecla"]
        if self.cfg["ativo"] and not (self._thread and self._thread.is_alive()):
            self._parar.clear()
            self._thread = threading.Thread(target=self._laco, name="ditado", daemon=True)
            self._thread.start()
        elif not self.cfg["ativo"]:
            self._parar.set()
            self.estado = "desligado"

    def resumo(self) -> dict[str, Any]:
        return {"estado": self.estado, "erro": self.erro, "ultimo": self.ultimo, "total": self.total,
                "config": self.cfg, "teclas": {k: v[1] for k, v in TECLAS.items()}}

    # ── gravação ───────────────────────────────────────────
    def _comecar(self) -> None:
        import sounddevice as sd
        self._partes = []

        def chegou(dados, _frames, _tempo, _status):
            self._partes.append(dados[:, 0].copy())

        self._stream = sd.InputStream(samplerate=SR, channels=1, dtype="float32", callback=chegou, blocksize=1600)
        self._stream.start()
        self._inicio = time.time()
        self.estado = "ouvindo"
        _bip(self.cfg["som"], 880, 70)

    def _terminar(self, descartar: bool = False) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        stream.stop()
        stream.close()
        duracao = time.time() - self._inicio
        audio = np.concatenate(self._partes) if self._partes else np.zeros(0, dtype="float32")
        if descartar or duracao < MIN_S or len(audio) < SR * MIN_S:
            self.estado = "pronto"
            return
        _bip(self.cfg["som"], 600, 60)
        self.estado = "transcrevendo"
        enter = bool(self.cfg["enter"])
        threading.Thread(target=self._finalizar, args=(audio, enter), daemon=True).start()

    def _finalizar(self, audio: np.ndarray, enter: bool) -> None:
        t0 = time.time()
        try:
            texto = (self._transcrever(_wav(audio)) or "").strip()
            if texto:
                digitar(texto, enter=enter)
                self.total += 1
            self.ultimo = {"texto": texto, "audio_s": round(len(audio) / SR, 1), "tempo_s": round(time.time() - t0, 2),
                           "quando": time.time()}
            self.erro = None
        except Exception as exc:  # noqa: BLE001
            self.erro = str(getattr(exc, "detail", None) or exc)
            log.warning("Ditado: %s", self.erro)
        finally:
            if self.estado == "transcrevendo":
                self.estado = "pronto" if self.cfg["ativo"] else "desligado"

    # ── atalho ─────────────────────────────────────────────
    def _laco(self) -> None:
        self.estado, self.erro = "pronto", None
        apertada_antes, desde, sujo = False, 0.0, False
        log.info("Ditado ligado: %s, modo %s.", TECLAS[self.cfg["tecla"]][1], self.cfg["modo"])
        while not self._parar.is_set():
            time.sleep(0.015)
            try:
                vk = TECLAS[self.cfg["tecla"]][0]
                agora_apertada = _apertada(vk)
                gravando = self._stream is not None
                if self.cfg["modo"] == "segurar":
                    if agora_apertada and not gravando and self.estado != "transcrevendo":
                        self._comecar()
                        sujo = False
                    elif agora_apertada and gravando and not sujo and _outra_tecla_apertada(vk):
                        sujo = True        # era um atalho (Ctrl+C...), não ditado: descarta
                    elif not agora_apertada and gravando:
                        self._terminar(descartar=sujo)
                    elif gravando and time.time() - self._inicio > MAX_S:
                        self._terminar()
                else:  # alternar: só um toque "limpo" conta (Ctrl+C com o Ctrl direito não dispara)
                    if agora_apertada and not apertada_antes:
                        desde, sujo = time.time(), False
                    elif agora_apertada and _outra_tecla_apertada(vk):
                        sujo = True
                    elif not agora_apertada and apertada_antes and not sujo and time.time() - desde <= TOQUE_MAX_S:
                        if gravando:
                            self._terminar()
                        elif self.estado != "transcrevendo":
                            self._comecar()
                    if gravando and time.time() - self._inicio > MAX_S:
                        self._terminar()
                apertada_antes = agora_apertada
            except Exception as exc:  # noqa: BLE001  (sem microfone, microfone ocupado...)
                self.erro = f"Microfone: {exc}"
                log.warning("Ditado: %s", self.erro)
                if self._stream is not None:
                    try:
                        self._stream.close()
                    except Exception:  # noqa: BLE001
                        pass
                    self._stream = None
                self.estado = "erro"
                time.sleep(2)
                self.estado = "pronto"
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        self.estado = "desligado"
        log.info("Ditado desligado.")
