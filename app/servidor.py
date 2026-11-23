"""STT Local: transcrição de áudio (Parakeet TDT 0.6B v3) no processador, numa porta só.

    INICIAR.bat            abre o serviço e a tela no navegador
    INICIAR_OCULTO.cmd     o mesmo, sem janela (é o que o "Iniciar com o Windows" usa)

Na mesma porta (padrão 8036):
  /                                     a tela de configuração e uso
  POST /v1/audio/transcriptions         contrato OpenAI (multipart: file + response_format;
                                        opcionais language, timestamp_granularities[]=word)
  POST /v1/audio/transcriptions/base64  JSON, para cliente que não sobe arquivo
  GET  /health, GET /v1/models
  /api/status, /api/config, /api/autostart, /api/parar    (usados pela tela)

Sem GPU: o modelo int8 roda no onnxruntime de CPU. Núcleos, por padrão, 1/4 dos
físicos, entre 1 e 8 (1-5 -> 1, 6-8 -> 2, 16 -> 4, 32+ -> 8).

Trava de silêncio (silencio.py, detector de fala Silero): só a fala vai para o modelo e
áudio sem fala volta com texto vazio. Sem ela, o Parakeet às vezes devolvia uma palavra
solta ("Yeah.") para um clipe só de ruído. Ligada por padrão; a tela desliga.

Saída com código 3 = "me reinicie": o .bat roda de novo (troca de porta ou de rede).
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import logging
import os
import socket
import sys
import threading
import time
import webbrowser
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

os.environ["CUDA_VISIBLE_DEVICES"] = ""  # só processador

import numpy as np  # noqa: E402
import psutil  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.concurrency import run_in_threadpool  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import baixar_modelo  # noqa: E402
import ditado  # noqa: E402
import mcp_stt  # noqa: E402
import silencio  # noqa: E402

VERSAO = "2.0.0-dev"
RAIZ = Path(__file__).resolve().parent.parent
APP = RAIZ / "app"
DADOS = RAIZ / "dados"
CONFIG = DADOS / "config.json"
PASTA_MODELO = RAIZ / "modelo" / "parakeet-tdt-0.6b-v3-int8"
MODELO_ID = "parakeet-tdt-0.6b-v3"
SILERO = RAIZ / "modelo" / "silero_vad_v6.onnx"
STARTUP_VBS = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "STT_Local.vbs"

SR = 16000
EXTENSOES = {".wav", ".mp3", ".m4a", ".ogg", ".oga", ".opus", ".flac", ".webm", ".mp4", ".mkv", ".aac", ".wma"}
FORMATOS = {"json", "verbose_json", "text"}
TRECHO_MAX_S = 45.0  # áudio mais longo é cortado na pausa mais funda entre 20 e 45 s
TRECHO_MIN_S = 20.0
REINICIAR = 3
MAX_ARQUIVO_MB = 500           # arquivo enviado
MAX_AUDIO_S = 2 * 3600          # áudio decodificado (2 h ≈ 460 MB de RAM)

DADOS.mkdir(exist_ok=True)
(DADOS / "logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-5s | %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("stt")


# ── Configuração ──────────────────────────────────────────

PADRAO = {"porta": 8036, "rede": False, "nucleos": "auto", "trava_silencio": True, "ditado": dict(ditado.PADRAO)}


def ler_config() -> dict[str, Any]:
    try:
        dados = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        dados = {}
    cfg = PADRAO | {k: v for k, v in dados.items() if k in PADRAO}
    try:
        cfg["porta"] = int(cfg["porta"])
    except (TypeError, ValueError):
        cfg["porta"] = PADRAO["porta"]
    cfg["rede"] = bool(cfg["rede"])
    cfg["trava_silencio"] = bool(cfg["trava_silencio"])
    dit = cfg["ditado"] if isinstance(cfg["ditado"], dict) else {}
    cfg["ditado"] = {**ditado.PADRAO, **{k: v for k, v in dit.items() if k in ditado.PADRAO}}
    if cfg["nucleos"] != "auto":
        try:
            cfg["nucleos"] = max(1, int(cfg["nucleos"]))
        except (TypeError, ValueError):
            cfg["nucleos"] = "auto"
    return cfg


def gravar_config(cfg: dict[str, Any]) -> None:
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(CONFIG)


def nucleos_fisicos() -> int:
    return psutil.cpu_count(logical=False) or max(1, (os.cpu_count() or 2) // 2)


def nucleos_automaticos(fisicos: int | None = None) -> int:
    """1/4 dos núcleos físicos, arredondado, entre 1 e 8. Acima de 8 o ganho some."""
    return max(1, min(8, int((fisicos or nucleos_fisicos()) / 4 + 0.5)))


def nucleos_da_config(cfg: dict[str, Any]) -> int:
    return nucleos_automaticos() if cfg["nucleos"] == "auto" else int(cfg["nucleos"])


# ── Estado ────────────────────────────────────────────────

class Estado:
    def __init__(self) -> None:
        self.modelo: Any = None
        self.detector: silencio.DetectorDeFala | None = None
        self.trava_ligada = True
        self.sem_fala = 0                      # pedidos barrados pela trava de silêncio
        self.nucleos = 0
        self.carregando = True
        self.erro_carga: str | None = None
        self.inicio = time.time()
        self.trava = threading.Lock()          # uma transcrição por vez
        self.pedidos = 0
        self.erros = 0
        self.segundos_audio = 0.0
        self.segundos_processo = 0.0
        self.ultima: dict[str, Any] | None = None
        self.cpu_processo = 0.0
        self.cpu_sistema = 0.0


E = Estado()
DOWNLOAD = baixar_modelo.Download()
DITADO = ditado.Ditado(lambda wav: _rodar(wav, "json", None, False)["text"])
PROC = psutil.Process()


def carregar_modelo(nucleos: int) -> None:
    import onnx_asr
    import onnxruntime as ort

    opcoes = ort.SessionOptions()
    opcoes.log_severity_level = 3
    opcoes.intra_op_num_threads = nucleos
    opcoes.inter_op_num_threads = 1
    t0 = time.perf_counter()
    modelo = onnx_asr.load_model(f"nemo-{MODELO_ID}", path=str(PASTA_MODELO), quantization="int8",
                                 sess_options=opcoes, providers=["CPUExecutionProvider"]).with_timestamps()
    with E.trava:
        E.modelo, E.nucleos = modelo, nucleos
    log.info("Modelo pronto em %.1fs (%d núcleo(s)).", time.perf_counter() - t0, nucleos)


def amostrar_cpu() -> None:
    """CPU do processo e do sistema, a cada 1 s (o psutil mede entre duas chamadas)."""
    PROC.cpu_percent(None)
    psutil.cpu_percent(None)
    while True:
        time.sleep(1.0)
        E.cpu_processo = PROC.cpu_percent(None) / (psutil.cpu_count() or 1)
        E.cpu_sistema = psutil.cpu_percent(None)


# ── Áudio e transcrição ───────────────────────────────────

def decodificar(conteudo: bytes) -> np.ndarray:
    """Qualquer formato (PyAV/ffmpeg) -> float32 mono 16 kHz."""
    import av

    pedacos: list[np.ndarray] = []
    try:
        with av.open(io.BytesIO(conteudo)) as container:
            if not container.streams.audio:
                raise HTTPException(400, "O arquivo não tem faixa de áudio.")
            reamostrador = av.AudioResampler(format="flt", layout="mono", rate=SR)
            for quadro in container.decode(audio=0):
                for saida in reamostrador.resample(quadro):
                    pedacos.append(saida.to_ndarray().reshape(-1))
            for saida in reamostrador.resample(None):
                pedacos.append(saida.to_ndarray().reshape(-1))
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Não deu para ler o áudio: {exc}") from exc
    return np.concatenate(pedacos).astype(np.float32) if pedacos else np.zeros(0, dtype=np.float32)


def trechos(audio: np.ndarray) -> list[tuple[int, int]]:
    total = len(audio)
    if total <= TRECHO_MAX_S * SR:
        return [(0, total)]
    passo = SR // 10
    n = total // passo
    energia = np.sqrt(np.mean(audio[: n * passo].reshape(n, passo) ** 2, axis=1))
    cortes, inicio = [], 0
    while total - inicio > TRECHO_MAX_S * SR:
        a = (inicio + int(TRECHO_MIN_S * SR)) // passo
        b = min((inicio + int(TRECHO_MAX_S * SR)) // passo, n)
        fim = (a + int(np.argmin(energia[a:b]))) * passo if b > a else inicio + int(TRECHO_MAX_S * SR)
        cortes.append((inicio, fim))
        inicio = fim
    cortes.append((inicio, total))
    return cortes


def blocos(audio: np.ndarray) -> list[tuple[int, int]]:
    """O que vai para o modelo: só as regiões com fala (trava ligada), em trechos de até 45 s."""
    if E.detector is None or not E.trava_ligada:
        return trechos(audio)
    saida: list[tuple[int, int]] = []
    for ini, fim in E.detector.regioes(audio):
        saida += [(ini + a, ini + b) for a, b in trechos(audio[ini:fim])]
    return saida


def palavras(tokens: list[str], tempos: list[float], deslocamento: float, fim_trecho: float) -> list[dict[str, Any]]:
    saida: list[dict[str, Any]] = []
    for token, t in zip(tokens, tempos):
        if not saida or token.startswith(" "):
            saida.append({"word": token, "start": deslocamento + float(t)})
        else:
            saida[-1]["word"] += token
    for i, p in enumerate(saida):
        p["end"] = saida[i + 1]["start"] if i + 1 < len(saida) else max(p["start"], fim_trecho)
    return saida


def transcrever(conteudo: bytes, formato: str, idioma: str | None, com_palavras: bool) -> Any:
    audio = decodificar(conteudo)
    if len(audio) == 0:
        raise HTTPException(400, "Áudio vazio ou ilegível.")
    duracao = len(audio) / SR
    if duracao > MAX_AUDIO_S:
        raise HTTPException(413, f"Áudio longo demais: {duracao / 60:.0f} min (máximo {MAX_AUDIO_S // 60} min).")
    t0 = time.perf_counter()
    segmentos: list[dict[str, Any]] = []
    todas: list[dict[str, Any]] = []
    with E.trava:
        if E.modelo is None:
            if not baixar_modelo.modelo_presente(PASTA_MODELO):
                raise HTTPException(503, "Modelo ainda não baixado. Abra a tela do STT Local e clique em \"Baixar modelo\".")
            raise HTTPException(503, "Modelo ainda carregando.")
        partes = blocos(audio)
        for ini, fim in partes:
            if fim - ini < SR // 10:
                continue
            r = E.modelo.recognize(audio[ini:fim], sample_rate=SR)
            texto = (r.text or "").strip()
            if texto:
                segmentos.append({"start": round(ini / SR, 3), "end": round(fim / SR, 3), "text": " " + texto})
                todas += palavras(list(r.tokens or []), list(r.timestamps or []), ini / SR, fim / SR)
    decorrido = time.perf_counter() - t0
    texto = "".join(s["text"] for s in segmentos)
    E.pedidos += 1
    if not partes:
        E.sem_fala += 1
    E.segundos_audio += duracao
    E.segundos_processo += decorrido
    E.ultima = {"duracao": round(duracao, 1), "tempo": round(decorrido, 2), "quando": time.time(),
                "velocidade": round(duracao / decorrido, 1) if decorrido else None}
    if not partes:
        log.info("SEM FALA | áudio %.1fs | trava de silêncio: texto vazio", duracao)
    else:
        log.info("OK | áudio %.1fs (fala %.1fs) em %.2fs (%.1fx) | %d núcleo(s)", duracao,
                 sum(b - a for a, b in partes) / SR, decorrido, duracao / decorrido if decorrido else 0, E.nucleos)

    if formato == "text":
        return PlainTextResponse(texto)
    if formato == "verbose_json":
        resposta: dict[str, Any] = {
            "task": "transcribe", "language": "pt" if (idioma or "").lower().startswith("pt") else (idioma or "auto"),
            "duration": round(duracao, 3), "elapsed": round(decorrido, 3), "text": texto,
            "segments": [{"id": i, **s} for i, s in enumerate(segmentos)],
        }
        if com_palavras:
            resposta["words"] = [{"word": p["word"], "start": round(p["start"], 3), "end": round(p["end"], 3),
                                  "probability": 1.0} for p in todas]
        return resposta
    return {"text": texto}


def _validar(nome: str | None, formato: str) -> None:
    if formato not in FORMATOS:
        raise HTTPException(400, f"response_format inválido: {formato!r}. Use: {sorted(FORMATOS)}")
    sufixo = Path(nome or "audio.wav").suffix.lower()
    if sufixo not in EXTENSOES:
        raise HTTPException(400, f"Formato não suportado: {sufixo}. Use: {sorted(EXTENSOES)}")


def _rodar(conteudo: bytes, formato: str, idioma: str | None, com_palavras: bool) -> Any:
    try:
        return transcrever(conteudo, formato, idioma, com_palavras)
    except HTTPException:
        E.erros += 1
        raise
    except Exception as exc:  # noqa: BLE001
        E.erros += 1
        log.exception("Erro na transcrição")
        raise HTTPException(500, f"Erro interno: {exc}") from exc


# ── App ───────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    threading.Thread(target=amostrar_cpu, daemon=True).start()

    def subir() -> None:
        try:
            cfg = ler_config()
            E.trava_ligada = cfg["trava_silencio"]
            try:
                E.detector = silencio.DetectorDeFala(SILERO)
            except Exception:  # noqa: BLE001
                log.exception("Trava de silêncio indisponível (%s): seguindo sem ela.", SILERO)
            DITADO.aplicar(cfg["ditado"])
            if baixar_modelo.modelo_presente(PASTA_MODELO):
                carregar_modelo(nucleos_da_config(cfg))
            else:
                log.info("Modelo ainda não baixado: use o botão \"Baixar modelo\" na tela.")
        except Exception as exc:  # noqa: BLE001
            E.erro_carga = f"{type(exc).__name__}: {exc}"
            log.exception("Falha ao carregar o modelo")
        finally:
            E.carregando = False

    threading.Thread(target=subir, daemon=True).start()
    yield


app = FastAPI(title="STT Local", version=VERSAO, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _cliente_local(request: Request) -> bool:
    return (request.client.host if request.client else "") in ("127.0.0.1", "::1", "localhost")


@app.middleware("http")
async def protege_rotas_da_tela(request: Request, call_next):
    """As rotas /api/ (configurar, desligar, iniciar com o Windows) só atendem a própria tela.

    A transcrição (/v1) fica aberta a qualquer programa, de propósito. Já as rotas da tela
    não podem ser chamadas por um site qualquer aberto no navegador: pedido com Origin de
    outro endereço é recusado. Sem Origin (curl, scripts), só desta máquina.
    """
    if request.url.path.startswith("/mcp"):
        # Proteção contra "DNS rebinding" exigida pelo MCP: navegador de outro site não entra.
        origem = request.headers.get("origin")
        if origem:
            from urllib.parse import urlsplit
            host_origem = (urlsplit(origem).hostname or "").lower()
            if host_origem not in ("127.0.0.1", "localhost", "::1") and urlsplit(origem).netloc.lower() != (request.headers.get("host") or "").lower():
                return JSONResponse({"detail": "Pedido de outro site recusado."}, status_code=403)
    if request.url.path.startswith("/api/"):
        origem = request.headers.get("origin")
        if origem:
            from urllib.parse import urlsplit
            if urlsplit(origem).netloc.lower() != (request.headers.get("host") or "").lower():
                return JSONResponse({"detail": "Pedido de outro site recusado."}, status_code=403)
        elif request.method not in ("GET", "HEAD") and not _cliente_local(request):
            return JSONResponse({"detail": "Só a tela ou esta máquina podem mudar o serviço."}, status_code=403)
    return await call_next(request)


class TranscricaoBase64(BaseModel):
    audio_base64: str
    filename: str = "audio.webm"
    response_format: str = "json"
    language: str | None = None


@app.post("/v1/audio/transcriptions")
async def rota_transcrever(
    file: UploadFile = File(...),
    response_format: str = Form("json"),
    language: str | None = Form(None),
    timestamp_granularities: list[str] | None = Form(None, alias="timestamp_granularities[]"),
    model: str | None = Form(None),  # o cliente OpenAI sempre manda; aceito e ignorado
    vad_filter: bool = Form(False),  # compatibilidade com o hub; quem manda é a trava de silêncio
):
    _validar(file.filename, response_format)
    conteudo = await file.read(MAX_ARQUIVO_MB * 2**20 + 1)
    if len(conteudo) > MAX_ARQUIVO_MB * 2**20:
        raise HTTPException(413, f"Arquivo maior que {MAX_ARQUIVO_MB} MB.")
    if not conteudo:
        raise HTTPException(400, "Áudio vazio.")
    return await run_in_threadpool(_rodar, conteudo, response_format, language,
                                   "word" in (timestamp_granularities or []))


@app.post("/v1/audio/transcriptions/base64")
async def rota_base64(pedido: TranscricaoBase64):
    _validar(pedido.filename, pedido.response_format)
    try:
        conteudo = base64.b64decode(pedido.audio_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(400, "audio_base64 inválido.") from exc
    if len(conteudo) > MAX_ARQUIVO_MB * 2**20:
        raise HTTPException(413, f"Arquivo maior que {MAX_ARQUIVO_MB} MB.")
    return await run_in_threadpool(_rodar, conteudo, pedido.response_format, pedido.language, False)


def estado_servico() -> str:
    if E.modelo is not None:
        return "no ar"
    if E.erro_carga:
        return "erro"
    if DOWNLOAD.ativo():
        return "baixando"
    if not baixar_modelo.modelo_presente(PASTA_MODELO):
        return "sem modelo"
    return "carregando"


@app.get("/health")
def health():
    st = estado_servico()
    return {"status": {"no ar": "ok", "sem modelo": "sem_modelo"}.get(st, st),
            "version": VERSAO, "model": MODELO_ID, "compute_type": "int8", "device": "cpu",
            "threads": E.nucleos, "trava_silencio": E.detector is not None and E.trava_ligada,
            "formatos": sorted(EXTENSOES)}


def _status_mcp() -> dict[str, Any]:
    porta = ler_config()["porta"]
    return {"estado": estado_servico(), "modelo": MODELO_ID, "versao": VERSAO, "nucleos": E.nucleos,
            "trava_silencio": E.detector is not None and E.trava_ligada,
            "endpoint": f"http://127.0.0.1:{porta}/v1/audio/transcriptions", "mcp": f"http://127.0.0.1:{porta}/mcp",
            "tela": f"http://127.0.0.1:{porta}/", "download": DOWNLOAD.resumo() if DOWNLOAD.ativo() else None}


MCP = mcp_stt.ServidorMCP(
    VERSAO,
    transcrever=lambda conteudo, formato, com_palavras: _rodar(conteudo, formato, None, com_palavras),
    status=_status_mcp, porta=lambda: ler_config()["porta"],
    extensoes=EXTENSOES, max_bytes=MAX_ARQUIVO_MB * 2**20,
)


@app.post("/mcp")
async def rota_mcp(request: Request):
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import Response
    try:
        mensagem = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "JSON inválido"}}, status_code=400)
    resposta = await run_in_threadpool(MCP.atender, mensagem, _cliente_local(request))
    if resposta is None:
        return Response(status_code=202)
    return JSONResponse(resposta)


@app.get("/mcp")
def rota_mcp_get():
    # Sem canal de eventos do servidor: o cliente usa só POST (permitido pelo transporte).
    return JSONResponse({"detail": "Use POST (Streamable HTTP, respostas em JSON)."}, status_code=405, headers={"Allow": "POST"})


@app.delete("/mcp")
def rota_mcp_delete():
    from starlette.responses import Response
    return Response(status_code=405)


@app.get("/v1/models")
def modelos():
    return {"object": "list", "data": [{"id": MODELO_ID, "object": "model", "owned_by": "stt-local"}]}


# ── Tela ──────────────────────────────────────────────────

@app.get("/")
def tela():
    return FileResponse(APP / "painel.html", headers={"Cache-Control": "no-store"})


def ips_locais() -> list[dict[str, str]]:
    achados = []
    for nome, enderecos in psutil.net_if_addrs().items():
        for e in enderecos:
            if e.family == socket.AF_INET and not e.address.startswith(("127.", "169.254.")):
                tipo = "tailnet" if e.address.startswith("100.") else "rede local"
                achados.append({"ip": e.address, "tipo": tipo, "interface": nome})
    return achados


@app.get("/api/status")
def status():
    cfg = ler_config()
    fisicos, logicos = nucleos_fisicos(), psutil.cpu_count() or 1
    return {
        "versao": VERSAO, "modelo": MODELO_ID,
        "estado": estado_servico(),
        "erro": E.erro_carga,
        "download": DOWNLOAD.resumo(),
        "ditado": DITADO.resumo(),
        "config": cfg,
        "nucleos": {"em_uso": E.nucleos, "automatico": nucleos_automaticos(fisicos), "fisicos": fisicos, "logicos": logicos},
        "uso": {"cpu_servico": round(E.cpu_processo, 1), "cpu_sistema": round(E.cpu_sistema, 1),
                "ram_servico_mb": round(PROC.memory_info().rss / 2**20),
                "ram_sistema_livre_mb": round(psutil.virtual_memory().available / 2**20)},
        "trava_silencio": {"ligada": E.trava_ligada, "disponivel": E.detector is not None},
        "contadores": {"transcricoes": E.pedidos, "erros": E.erros, "sem_fala": E.sem_fala, "audio_s": round(E.segundos_audio, 1),
                       "processo_s": round(E.segundos_processo, 2),
                       "velocidade_media": round(E.segundos_audio / E.segundos_processo, 1) if E.segundos_processo else None,
                       "ultima": E.ultima},
        "no_ar_desde": E.inicio,
        "enderecos": ips_locais() if cfg["rede"] else [],
        "autostart": STARTUP_VBS.exists(),
    }


class PedidoConfig(BaseModel):
    porta: int | None = None
    rede: bool | None = None
    nucleos: str | int | None = None
    trava_silencio: bool | None = None


def _sair_depois(codigo: int, atraso: float = 0.6) -> None:
    def sair() -> None:
        time.sleep(atraso)
        os._exit(codigo)

    threading.Thread(target=sair, daemon=True).start()


@app.post("/api/config")
def mudar_config(pedido: PedidoConfig):
    cfg = ler_config()
    novo = dict(cfg)
    if pedido.porta is not None:
        if not 1024 <= pedido.porta <= 65535:
            raise HTTPException(400, "Porta fora da faixa (1024-65535).")
        novo["porta"] = pedido.porta
    if pedido.rede is not None:
        novo["rede"] = pedido.rede
    if pedido.nucleos is not None:
        if str(pedido.nucleos) == "auto":
            novo["nucleos"] = "auto"
        else:
            try:
                n = int(pedido.nucleos)
            except ValueError as exc:
                raise HTTPException(400, "Núcleos: 'auto' ou um número.") from exc
            novo["nucleos"] = max(1, min(n, psutil.cpu_count() or n))
    if pedido.trava_silencio is not None:
        novo["trava_silencio"] = pedido.trava_silencio
        E.trava_ligada = pedido.trava_silencio  # vale no próximo pedido, sem reiniciar
    gravar_config(novo)

    reiniciar = (novo["porta"], novo["rede"]) != (cfg["porta"], cfg["rede"])
    if reiniciar:
        log.info("Porta/rede mudou: reiniciando em %d (%s).", novo["porta"], "rede" if novo["rede"] else "só local")
        _sair_depois(REINICIAR)
    elif nucleos_da_config(novo) != E.nucleos and E.modelo is not None:
        threading.Thread(target=carregar_modelo, args=(nucleos_da_config(novo),), daemon=True).start()
    return {"config": novo, "reiniciando": reiniciar}


class PedidoAutostart(BaseModel):
    ativo: bool


@app.post("/api/autostart")
def autostart(pedido: PedidoAutostart):
    if pedido.ativo:
        cmd = RAIZ / "INICIAR_OCULTO.cmd"
        STARTUP_VBS.parent.mkdir(parents=True, exist_ok=True)
        STARTUP_VBS.write_text(
            "' STT Local no logon, sem janela. Para desligar, desmarque na tela ou apague este arquivo.\r\n"
            # Em VBScript "" dentro da string vira uma aspa: o resultado e  cmd.exe /d /c "C:\...\INICIAR_OCULTO.cmd"
            'CreateObject("WScript.Shell").Run "cmd.exe /d /c ""' + str(cmd) + '""", 0, False\r\n',
            encoding="ascii", errors="replace")  # VBScript le ANSI; caminho com acento viraria "?"
    elif STARTUP_VBS.exists():
        STARTUP_VBS.unlink()
    return {"autostart": STARTUP_VBS.exists()}


def _modelo_baixado() -> None:
    log.info("Modelo baixado e conferido. Carregando…")
    E.erro_carga, E.carregando = None, True
    try:
        carregar_modelo(nucleos_da_config(ler_config()))
    except Exception as exc:  # noqa: BLE001
        E.erro_carga = f"{type(exc).__name__}: {exc}"
        log.exception("Falha ao carregar o modelo")
    finally:
        E.carregando = False


@app.get("/api/conectar")
def conectar(cliente: str = "openai"):
    return PlainTextResponse(mcp_stt.instrucoes(cliente, ler_config()["porta"]))


@app.post("/api/modelo/baixar")
def modelo_baixar():
    if E.modelo is not None or baixar_modelo.modelo_presente(PASTA_MODELO):
        return {"ok": True, "detalhe": "O modelo já está baixado."}
    DOWNLOAD.iniciar(PASTA_MODELO, ao_terminar=_modelo_baixado)
    return {"ok": True, "download": DOWNLOAD.resumo()}


@app.post("/api/modelo/cancelar")
def modelo_cancelar():
    DOWNLOAD.cancelar()
    return {"ok": True}


class PedidoDitado(BaseModel):
    ativo: bool | None = None
    tecla: str | None = None
    modo: str | None = None
    enter: bool | None = None
    som: bool | None = None


@app.post("/api/ditado")
def mudar_ditado(pedido: PedidoDitado):
    cfg = ler_config()
    novo = dict(cfg["ditado"])
    for campo, valor in pedido.model_dump(exclude_none=True).items():
        novo[campo] = valor
    if novo["tecla"] not in ditado.TECLAS:
        raise HTTPException(400, f"Tecla inválida. Opções: {', '.join(ditado.TECLAS)}")
    if novo["modo"] not in ("segurar", "alternar"):
        raise HTTPException(400, "Modo: 'segurar' ou 'alternar'.")
    cfg["ditado"] = novo
    gravar_config(cfg)
    DITADO.aplicar(novo)
    return DITADO.resumo()


@app.post("/api/parar")
def parar():
    log.info("Parado pela tela.")
    _sair_depois(0, 0.3)
    return {"parando": True}


# ── Subida ────────────────────────────────────────────────

def porta_ocupada(porta: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", porta)) == 0


def main() -> int:
    cfg = ler_config()
    if not CONFIG.exists():
        gravar_config(cfg)
    porta, host = cfg["porta"], "0.0.0.0" if cfg["rede"] else "127.0.0.1"
    tela_url = f"http://127.0.0.1:{porta}/"
    abrir = "--abrir" in sys.argv
    if porta_ocupada(porta):
        print(f"  A porta {porta} já está em uso (o STT Local já está aberto?). Tela: {tela_url}")
        if abrir:
            webbrowser.open(tela_url)
        return 0
    if not baixar_modelo.modelo_presente(PASTA_MODELO):
        print("  Primeira vez? O modelo (~640 MB) ainda não foi baixado: clique em \"Baixar modelo\" na tela.")
    print(f"\n  STT Local {VERSAO} — Parakeet TDT 0.6B v3 no processador")
    print(f"  Tela:      {tela_url}")
    print(f"  Endpoint:  http://127.0.0.1:{porta}/v1/audio/transcriptions" + ("   (aceitando da rede)" if cfg["rede"] else ""))
    print("  Feche esta janela para desligar.\n")
    if abrir:
        threading.Timer(1.5, lambda: webbrowser.open(tela_url)).start()
    uvicorn.run(app, host=host, port=porta, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
