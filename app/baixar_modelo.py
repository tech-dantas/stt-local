"""Baixa o modelo Parakeet TDT 0.6B v3 (int8, ONNX) da fonte oficial no Hugging Face.

- Fonte fixa numa revisão (não muda por baixo da gente) e cada arquivo é conferido:
  os .onnx pelo SHA-256 publicado; config.json e vocab.txt pelo hash do git.
- Retoma de onde parou: um download interrompido continua do byte em que estava.
- Grava em <pasta>.parcial e só move para a pasta final quando TUDO conferir; assim
  o serviço nunca tenta abrir um modelo pela metade.
- Só biblioteca padrão (urllib), para rodar no Python embutido sem pacote extra.

Uso solto (sem a tela):  runtime\\python.exe app\\baixar_modelo.py
"""
from __future__ import annotations

import hashlib
import os
import shutil
import ssl
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO = "istupakov/parakeet-tdt-0.6b-v3-onnx"
REVISAO = "8f23f0c03c8761650bdb5b40aaf3e40d2c15f1ce"
URL = f"https://huggingface.co/{REPO}/resolve/{REVISAO}/{{nome}}"

# nome, tamanho em bytes, tipo de hash, hash esperado
ARQUIVOS: list[tuple[str, int, str, str]] = [
    ("config.json", 97, "git", "02a773005666393de591dfd55230b778da1653d8"),
    ("vocab.txt", 93_939, "git", "fc43e1c723e262df60b70e1919614417162d1fe2"),
    ("nemo128.onnx", 139_764, "sha256", "a9fde1486ebfcc08f328d75ad4610c67835fea58c73ba57e3209a6f6cf019e9f"),
    ("decoder_joint-model.int8.onnx", 18_202_004, "sha256", "eea7483ee3d1a30375daedc8ed83e3960c91b098812127a0d99d1c8977667a70"),
    ("encoder-model.int8.onnx", 652_183_999, "sha256", "6139d2fa7e1b086097b277c7149725edbab89cc7c7ae64b23c741be4055aff09"),
]
TOTAL = sum(a[1] for a in ARQUIVOS)
BLOCO = 1 << 20


def modelo_presente(pasta: Path) -> bool:
    """Pasta final completa (os tamanhos batem). A conferência de hash é feita no download."""
    return all((pasta / nome).is_file() and (pasta / nome).stat().st_size == tam for nome, tam, _, _ in ARQUIVOS)


def _hash(caminho: Path, tipo: str) -> str:
    h = hashlib.sha256() if tipo == "sha256" else hashlib.sha1(f"blob {caminho.stat().st_size}\0".encode())
    with caminho.open("rb") as f:
        while bloco := f.read(BLOCO):
            h.update(bloco)
    return h.hexdigest()


class Download:
    """Estado de um download, lido pela tela em /api/status."""

    def __init__(self) -> None:
        self.estado = "parado"          # parado | baixando | conferindo | pronto | erro | cancelado
        self.baixado = 0
        self.total = TOTAL
        self.arquivo = ""
        self.velocidade = 0.0           # bytes/s
        self.erro: str | None = None
        self.iniciado = 0.0
        self._cancelar = threading.Event()
        self._thread: threading.Thread | None = None

    def resumo(self) -> dict[str, Any]:
        falta = self.total - self.baixado
        return {
            "estado": self.estado, "baixado_mb": round(self.baixado / 2**20, 1),
            "total_mb": round(self.total / 2**20, 1),
            "porcento": round(100 * self.baixado / self.total, 1) if self.total else 0,
            "arquivo": self.arquivo, "velocidade_mbs": round(self.velocidade / 2**20, 2),
            "restante_s": round(falta / self.velocidade) if self.velocidade > 1 and self.estado == "baixando" else None,
            "erro": self.erro, "fonte": f"huggingface.co/{REPO}",
        }

    def ativo(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def iniciar(self, pasta: Path, ao_terminar=None) -> bool:
        if self.ativo():
            return False
        self._cancelar.clear()
        self.erro, self.estado, self.iniciado = None, "baixando", time.time()
        self._thread = threading.Thread(target=self._rodar, args=(pasta, ao_terminar), daemon=True)
        self._thread.start()
        return True

    def cancelar(self) -> None:
        self._cancelar.set()

    # ── trabalho ────────────────────────────────────────────
    def _rodar(self, pasta: Path, ao_terminar) -> None:
        parcial = pasta.with_name(pasta.name + ".parcial")
        try:
            parcial.mkdir(parents=True, exist_ok=True)
            self.baixado = sum(min((parcial / n).stat().st_size, t) for n, t, _, _ in ARQUIVOS if (parcial / n).exists())
            for nome, tam, tipo, esperado in ARQUIVOS:
                destino = parcial / nome
                self.arquivo = nome
                if not (destino.exists() and destino.stat().st_size == tam):
                    self._baixar(nome, tam, destino)
                self.estado = "conferindo"
                if _hash(destino, tipo) != esperado:
                    destino.unlink(missing_ok=True)
                    raise RuntimeError(f"{nome} chegou corrompido (hash não confere). Tente de novo.")
                self.estado = "baixando"
            # Move arquivo a arquivo (renomear a pasta falha se o Windows ou o Explorer
            # estiver com ela aberta).
            pasta.mkdir(parents=True, exist_ok=True)
            for nome, _, _, _ in ARQUIVOS:
                os.replace(parcial / nome, pasta / nome)
            shutil.rmtree(parcial, ignore_errors=True)
            self.baixado, self.estado, self.arquivo = self.total, "pronto", ""
            if ao_terminar:
                ao_terminar()
        except _Cancelado:
            self.estado = "cancelado"
        except Exception as exc:  # noqa: BLE001
            self.estado, self.erro = "erro", _mensagem(exc)

    def _baixar(self, nome: str, tam: int, destino: Path) -> None:
        ja = destino.stat().st_size if destino.exists() else 0
        if ja > tam:
            destino.unlink()
            self.baixado -= ja
            ja = 0
        pedido = urllib.request.Request(URL.format(nome=nome), headers={"User-Agent": "stt-local"})
        if ja:
            pedido.add_header("Range", f"bytes={ja}-")
        for tentativa in range(5):
            try:
                with urllib.request.urlopen(pedido, timeout=30, context=ssl.create_default_context()) as r:
                    if ja and r.status != 206:      # servidor ignorou o Range: recomeça o arquivo
                        self.baixado -= ja
                        ja = 0
                    modo = "ab" if ja else "wb"
                    janela_t, janela_b = time.time(), 0
                    with destino.open(modo) as f:
                        while bloco := r.read(BLOCO):
                            if self._cancelar.is_set():
                                raise _Cancelado
                            f.write(bloco)
                            ja += len(bloco)
                            self.baixado += len(bloco)
                            janela_b += len(bloco)
                            agora = time.time()
                            if agora - janela_t >= 1:
                                self.velocidade = janela_b / (agora - janela_t)
                                janela_t, janela_b = agora, 0
                if ja == tam:
                    return
                raise ConnectionError(f"{nome}: conexão terminou em {ja} de {tam} bytes")
            except _Cancelado:
                raise
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
                if tentativa == 4:
                    raise
                time.sleep(2 * (tentativa + 1))
                ja = destino.stat().st_size if destino.exists() else 0
                pedido = urllib.request.Request(URL.format(nome=nome), headers={"User-Agent": "stt-local", "Range": f"bytes={ja}-"})


class _Cancelado(Exception):
    pass


def _mensagem(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"O Hugging Face respondeu {exc.code}. Tente de novo em alguns minutos."
    if isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError)):
        return "Sem conexão com o Hugging Face. Confira a internet e tente de novo (o download continua de onde parou)."
    if isinstance(exc, OSError) and (getattr(exc, "winerror", None) == 112 or "No space" in str(exc)):
        return "Disco cheio: o modelo precisa de uns 700 MB livres."
    return f"{type(exc).__name__}: {exc}"


if __name__ == "__main__":
    import sys
    destino = Path(__file__).resolve().parent.parent / "modelo" / "parakeet-tdt-0.6b-v3-int8"
    if modelo_presente(destino):
        print("Modelo já está baixado em", destino)
        sys.exit(0)
    d = Download()
    d.iniciar(destino)
    while d.ativo():
        r = d.resumo()
        print(f"\r  {r['porcento']:5.1f}%  {r['baixado_mb']:.0f}/{r['total_mb']:.0f} MB  {r['velocidade_mbs']:.1f} MB/s   ", end="", flush=True)
        time.sleep(0.5)
    print("\n ", d.resumo()["estado"], d.erro or "")
    sys.exit(0 if d.estado == "pronto" else 1)
