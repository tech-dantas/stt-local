"""MCP embutido do STT Local (transporte Streamable HTTP, em /mcp).

Qualquer agente que fale MCP (Hermes, OpenCode, Claude Code, Codex...) se conecta com
uma URL só: http://127.0.0.1:<porta>/mcp. Sem pacote extra: é JSON-RPC 2.0 por POST,
respondendo em application/json (o transporte permite isso para quem não precisa de stream).

Ferramentas:
- transcrever_arquivo   transcreve um arquivo desta máquina (só para quem chama daqui)
- transcrever_base64    transcreve um áudio mandado em base64
- status_do_stt         o serviço está pronto? modelo, núcleos, endereços
- como_conectar         o passo a passo exato para ligar um programa ao STT Local
"""
from __future__ import annotations

import base64
import binascii
import json
from pathlib import Path
from typing import Any, Callable

PROTOCOLOS = ("2025-06-18", "2025-03-26", "2024-11-05")
NOME = "stt-local"

FERRAMENTAS: list[dict[str, Any]] = [
    {
        "name": "transcrever_arquivo",
        "description": ("Transcreve (fala → texto) um arquivo de áudio ou vídeo que está NESTA máquina, "
                        "no processador, sem internet. Aceita wav, mp3, ogg/opus (áudio do WhatsApp/Telegram), "
                        "m4a, flac, webm, mp4, mkv, aac, wma. Português e mais 24 línguas, detectadas sozinhas."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "caminho": {"type": "string", "description": "Caminho completo do arquivo, ex.: C:\\\\Users\\\\voce\\\\audio.ogg"},
                "com_tempos": {"type": "boolean", "description": "true = devolve também os trechos com início e fim (s)", "default": False},
            },
            "required": ["caminho"],
        },
    },
    {
        "name": "transcrever_base64",
        "description": "Transcreve um áudio enviado em base64 (para quem não está na mesma máquina do arquivo).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "audio_base64": {"type": "string", "description": "Conteúdo do arquivo em base64"},
                "nome": {"type": "string", "description": "Nome com extensão, ex.: fala.ogg", "default": "audio.wav"},
                "com_tempos": {"type": "boolean", "default": False},
            },
            "required": ["audio_base64"],
        },
    },
    {
        "name": "status_do_stt",
        "description": "Diz se o STT Local está pronto para transcrever, qual modelo, quantos núcleos e os endereços (endpoint e MCP).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "como_conectar",
        "description": ("Devolve o passo a passo e a configuração EXATA para ligar um programa ao STT Local: "
                        "hermes (transcrição da voz do usuário e/ou MCP), opencode, claude-code, codex ou openai "
                        "(qualquer cliente da API da OpenAI). Use quando o usuário pedir para configurar a transcrição."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "cliente": {"type": "string", "enum": ["hermes", "opencode", "claude-code", "codex", "openai"]},
            },
            "required": ["cliente"],
        },
    },
]


def instrucoes(cliente: str, porta: int) -> str:
    base = f"http://127.0.0.1:{porta}"
    mcp = f"{base}/mcp"
    textos = {
        "hermes": f"""Hermes Agent — duas ligações independentes:

1) Transcrição da voz do usuário (microfone do app, áudios do Telegram/WhatsApp).
   No config.yaml do perfil (faça backup antes), deixe a seção stt assim:

stt:
  enabled: true
  provider: openai
  openai:
    base_url: {base}/v1
    api_key: sem-chave
    model: parakeet-tdt-0.6b-v3

   Depois reinicie o gateway (hermes gateway restart) e reabra o app.

2) Ferramentas de transcrição para o agente (MCP):
   hermes mcp add stt-local --url {mcp}
""",
        "opencode": f"""OpenCode — no opencode.json (do projeto ou em ~/.config/opencode/opencode.json),
acrescente dentro de "mcp":

{{
  "mcp": {{
    "stt-local": {{
      "type": "remote",
      "url": "{mcp}",
      "enabled": true
    }}
  }}
}}

Reabra o OpenCode. Para DITAR no OpenCode (falar em vez de digitar), use o Ditado do STT Local:
atalho de teclado que escreve onde o cursor estiver, em qualquer programa.""",
        "claude-code": f"""Claude Code — no terminal:

claude mcp add --transport http stt-local {mcp}

Para ditar no Claude Code, use o Ditado do STT Local (atalho que escreve onde o cursor estiver).""",
        "codex": f"""Codex CLI — no ~/.codex/config.toml:

[mcp_servers.stt-local]
url = "{mcp}"
""",
        "openai": f"""Qualquer cliente da API da OpenAI (Python, Node, n8n, apps):

base_url = {base}/v1
api_key  = qualquer texto
model    = parakeet-tdt-0.6b-v3

Exemplo em Python:
from openai import OpenAI
cliente = OpenAI(base_url="{base}/v1", api_key="sem-chave")
texto = cliente.audio.transcriptions.create(model="parakeet-tdt-0.6b-v3", file=open("audio.ogg", "rb")).text

curl:
curl -X POST {base}/v1/audio/transcriptions -F "file=@audio.ogg" -F "response_format=json"
""",
    }
    return textos.get(cliente, "Cliente desconhecido. Opções: " + ", ".join(textos))


class ServidorMCP:
    """Atende JSON-RPC do MCP. `transcrever(bytes, formato, com_palavras)` e `status()` vêm do servidor."""

    def __init__(self, versao: str, transcrever: Callable[..., Any], status: Callable[[], dict[str, Any]],
                 porta: Callable[[], int], extensoes: set[str], max_bytes: int) -> None:
        self.versao, self._transcrever, self._status, self._porta = versao, transcrever, status, porta
        self.extensoes, self.max_bytes = extensoes, max_bytes

    # ── JSON-RPC ───────────────────────────────────────────
    def atender(self, mensagem: Any, local: bool) -> Any:
        if isinstance(mensagem, list):
            respostas = [r for r in (self._uma(m, local) for m in mensagem) if r is not None]
            return respostas or None
        return self._uma(mensagem, local)

    def _uma(self, m: Any, local: bool) -> dict[str, Any] | None:
        if not isinstance(m, dict) or m.get("jsonrpc") != "2.0" or "method" not in m:
            return _erro(m.get("id") if isinstance(m, dict) else None, -32600, "Pedido JSON-RPC inválido")
        ident, metodo, params = m.get("id"), m["method"], m.get("params") or {}
        if ident is None:                      # notificação (ex.: notifications/initialized): sem resposta
            return None
        try:
            if metodo == "initialize":
                pedida = params.get("protocolVersion")
                return _ok(ident, {
                    "protocolVersion": pedida if pedida in PROTOCOLOS else PROTOCOLOS[0],
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": NOME, "title": "STT Local", "version": self.versao},
                    "instructions": ("Transcrição de fala para texto, local, no processador (Parakeet TDT 0.6B v3). "
                                     "Use transcrever_arquivo para arquivos desta máquina e como_conectar para "
                                     "configurar outros programas."),
                })
            if metodo == "ping":
                return _ok(ident, {})
            if metodo == "tools/list":
                return _ok(ident, {"tools": FERRAMENTAS})
            if metodo == "tools/call":
                return _ok(ident, self._chamar(str(params.get("name") or ""), params.get("arguments") or {}, local))
            return _erro(ident, -32601, f"Método não suportado: {metodo}")
        except Exception as exc:  # noqa: BLE001
            return _erro(ident, -32603, f"{type(exc).__name__}: {exc}")

    # ── ferramentas ────────────────────────────────────────
    def _chamar(self, nome: str, args: dict[str, Any], local: bool) -> dict[str, Any]:
        try:
            if nome == "status_do_stt":
                return _texto(json.dumps(self._status(), ensure_ascii=False, indent=2))
            if nome == "como_conectar":
                return _texto(instrucoes(str(args.get("cliente", "")), self._porta()))
            if nome == "transcrever_arquivo":
                if not local:
                    return _falha("transcrever_arquivo só atende quem está nesta máquina. De outro computador, use transcrever_base64.")
                caminho = Path(str(args.get("caminho", ""))).expanduser()
                if caminho.suffix.lower() not in self.extensoes:
                    return _falha(f"Extensão não suportada: {caminho.suffix or '(nenhuma)'}. Aceitas: {', '.join(sorted(self.extensoes))}")
                if not caminho.is_file():
                    return _falha(f"Arquivo não encontrado: {caminho}")
                if caminho.stat().st_size > self.max_bytes:
                    return _falha(f"Arquivo grande demais (máximo {self.max_bytes // 2**20} MB).")
                return self._resultado(caminho.read_bytes(), bool(args.get("com_tempos")))
            if nome == "transcrever_base64":
                try:
                    conteudo = base64.b64decode(str(args.get("audio_base64", "")), validate=True)
                except (binascii.Error, ValueError):
                    return _falha("audio_base64 inválido.")
                if len(conteudo) > self.max_bytes:
                    return _falha(f"Áudio grande demais (máximo {self.max_bytes // 2**20} MB).")
                return self._resultado(conteudo, bool(args.get("com_tempos")))
            return _falha(f"Ferramenta desconhecida: {nome}")
        except Exception as exc:  # noqa: BLE001  (HTTPException do servidor vira texto para o agente)
            return _falha(str(getattr(exc, "detail", None) or exc))

    def _resultado(self, conteudo: bytes, com_tempos: bool) -> dict[str, Any]:
        r = self._transcrever(conteudo, "verbose_json" if com_tempos else "json", False)
        if com_tempos:
            return _texto(json.dumps({k: r[k] for k in ("text", "duration", "elapsed", "segments") if k in r}, ensure_ascii=False))
        return _texto((r.get("text") or "").strip() or "(sem fala no áudio)")


def _ok(ident: Any, resultado: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": ident, "result": resultado}


def _erro(ident: Any, codigo: int, mensagem: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": ident, "error": {"code": codigo, "message": mensagem}}


def _texto(t: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": t}], "isError": False}


def _falha(t: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": t}], "isError": True}
