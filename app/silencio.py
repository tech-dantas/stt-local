"""Trava de silencio do Parakeet: so manda para o modelo o que for fala.

O Parakeet, em trecho sem fala (ruido de sala, respiracao, clique do microfone,
clipe curto do push-to-talk), as vezes devolve uma palavra solta que ninguem disse
("Yeah.", "Mm-hmm.", "Obrigado."). O Whisper do hub tem o mesmo problema e a mesma
cura: o detector de fala Silero (MIT), que o faster-whisper ja traz em
`faster_whisper/assets/silero_vad_v6.onnx`. Este modulo usa esse mesmo arquivo.

Como funciona:
1. O Silero da uma probabilidade de fala a cada 32 ms (512 amostras a 16 kHz).
2. As regioes com fala viram trechos, com uma folga nas pontas e pausas curtas
   coladas (a frase nao e picotada).
3. Sem regiao de fala nenhuma, a resposta e texto vazio, sem chamar o modelo.
4. O silencio longo entre falas fica de fora, entao tambem nao vira texto.

Custo: ~1 ms por segundo de audio num nucleo; o arquivo do modelo tem 1,2 MB.
Arquivo igual no hub (services/parakeet) e no STT Portatil (app/). O .onnx fica em
models/ (hub) e modelo/ (portatil), com o do faster-whisper como reserva no hub.
"""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

SR = 16000
QUADRO = 512              # 32 ms por probabilidade
CONTEXTO = 64             # amostras do quadro anterior que o Silero v6 recebe junto

LIMIAR = 0.5              # prob >= isto abre fala (o padrao do Silero e do faster-whisper)
LIMIAR_FIM = 0.35         # prob < isto conta como silencio (histerese)
FALA_MIN_S = 0.25         # regiao de fala mais curta que isto e descartada (clique, estalo)
SILENCIO_FECHA_S = 0.5    # silencio que encerra uma regiao
FOLGA_S = 0.3             # margem antes e depois de cada regiao (nao corta a primeira silaba)
JUNTA_S = 1.0             # regioes separadas por menos que isto viram um trecho so


class DetectorDeFala:
    """Silero VAD v6 (o export do faster-whisper: entradas input/h/c)."""

    def __init__(self, caminho: Path | str) -> None:
        import onnxruntime as ort

        opcoes = ort.SessionOptions()
        opcoes.intra_op_num_threads = 1   # e leve; nao disputa nucleo com o Parakeet
        opcoes.inter_op_num_threads = 1
        opcoes.enable_cpu_mem_arena = False
        opcoes.log_severity_level = 4
        self.caminho = Path(caminho)
        self._sessao = ort.InferenceSession(str(caminho), sess_options=opcoes, providers=["CPUExecutionProvider"])
        self._trava = threading.Lock()

    def probabilidades(self, audio: np.ndarray) -> np.ndarray:
        """Uma probabilidade de fala (0-1) a cada 32 ms."""
        resto = len(audio) % QUADRO
        if resto:
            audio = np.pad(audio, (0, QUADRO - resto))
        if len(audio) == 0:
            return np.zeros(0, dtype=np.float32)
        quadros = audio.astype(np.float32, copy=False).reshape(-1, QUADRO)
        contexto = np.roll(quadros[:, -CONTEXTO:], 1, axis=0)
        contexto[0] = 0
        entrada = np.ascontiguousarray(np.concatenate([contexto, quadros], axis=1))
        h = np.zeros((1, 1, 128), dtype=np.float32)
        c = np.zeros((1, 1, 128), dtype=np.float32)
        saidas = []
        with self._trava:
            for i in range(0, len(entrada), 10000):
                p, h, c = self._sessao.run(None, {"input": entrada[i : i + 10000], "h": h, "c": c})
                saidas.append(np.asarray(p, dtype=np.float32).reshape(-1))
        return np.concatenate(saidas)

    def regioes(self, audio: np.ndarray) -> list[tuple[int, int]]:
        """Trechos (amostra inicial, final) com fala, ja com folga e pausas curtas coladas."""
        probs = self.probabilidades(audio)
        brutas: list[tuple[int, int]] = []
        em_fala, inicio, silencio_desde = False, 0, -1
        fecha = int(SILENCIO_FECHA_S * SR / QUADRO)
        for i, p in enumerate(probs):
            if not em_fala:
                if p >= LIMIAR:
                    em_fala, inicio, silencio_desde = True, i, -1
            elif p < LIMIAR_FIM:
                if silencio_desde < 0:
                    silencio_desde = i
                if i - silencio_desde >= fecha:
                    brutas.append((inicio, silencio_desde))
                    em_fala = False
            elif p >= LIMIAR:
                silencio_desde = -1
        if em_fala:
            brutas.append((inicio, silencio_desde if silencio_desde >= 0 else len(probs)))

        total = len(audio)
        folga, junta = int(FOLGA_S * SR), int(JUNTA_S * SR)
        regioes: list[tuple[int, int]] = []
        for a, b in brutas:
            if (b - a) * QUADRO < FALA_MIN_S * SR:
                continue
            ini, fim = max(0, a * QUADRO - folga), min(total, b * QUADRO + folga)
            if regioes and ini - regioes[-1][1] < junta:
                regioes[-1] = (regioes[-1][0], fim)
            else:
                regioes.append((ini, fim))
        return regioes


def caminho_padrao(*candidatos: Path) -> Path | None:
    """O primeiro arquivo do Silero que existir: os candidatos, depois o do faster-whisper."""
    for c in candidatos:
        if c and Path(c).is_file():
            return Path(c)
    try:
        import importlib.util

        spec = importlib.util.find_spec("faster_whisper")
        for pasta in (spec.submodule_search_locations or []) if spec else []:
            arquivo = Path(pasta) / "assets" / "silero_vad_v6.onnx"
            if arquivo.is_file():
                return arquivo
    except Exception:  # noqa: BLE001
        pass
    return None
