# 🎙️ STT Local

**Sua fala vira texto no seu próprio computador.** Pelo processador, sem placa de vídeo, sem conta e sem mandar áudio para a internet.

- **Ditado em qualquer programa:** aperte um atalho, fale, e o texto aparece onde o cursor estiver (OpenCode, Claude Code, navegador, Word, WhatsApp Web…).
- **Para agentes:** um MCP embutido que Hermes, OpenCode, Claude Code e Codex usam com uma linha de configuração.
- **Para programadores:** mesmo formato da API de transcrição da OpenAI. Quem já usa Whisper só troca o endereço.
- **Português de verdade:** modelo Parakeet TDT 0.6B v3 (NVIDIA), com pontuação, mais 24 línguas detectadas sozinhas.

> Windows 10/11 de 64 bits · ~2 GB de RAM livre · ~1 GB de disco

---

## Começar (3 passos)

1. **Baixe** o arquivo `stt-local-…-windows.zip` em [Releases](../../releases) e extraia onde quiser.
2. Dê dois cliques em **`INICIAR.bat`**. A tela abre no navegador (`http://127.0.0.1:8036`).
3. Clique em **Baixar modelo** (uma vez só, ~640 MB, da fonte oficial). Pronto: teste na própria tela.

Nada para instalar: o Python e tudo o que ele precisa já vêm na pasta.

## Ditado em qualquer programa

Na tela, abra **Ditado** e ligue. Depois:

| Modo | Como usar |
|---|---|
| **Segurar para falar** (padrão) | Segure **Ctrl direito**, fale e solte. |
| **Tocar para começar e parar** | Um toque começa, outro termina. Bom para falas longas. |

- Uma **onda de áudio flutuante**, estilo rádio, aparece na parte de baixo da tela: barras que acompanham a sua voz enquanto grava, onda azul enquanto escreve e ✓ quando termina. Ela nunca pega o foco do programa onde você está.
- O texto é digitado onde o cursor está, com acentos, sem mexer na sua área de transferência.
- Opcional: **Enter automático** no fim, para enviar a mensagem sozinho.
- Atalhos como Ctrl+C continuam normais; um toque acidental curto é ignorado.
- A tecla pode ser Ctrl direito, AltGr, F8, F9, F10, Pause ou Scroll Lock.
- Programas abertos "como administrador" não aceitam o texto (proteção do Windows).

## Conectar agentes (MCP)

O endereço do MCP é `http://127.0.0.1:8036/mcp`. Na tela, a aba **Conectar** mostra a configuração pronta para copiar.

**Hermes Agent**
```
hermes mcp add stt-local --url http://127.0.0.1:8036/mcp
```
E para o Hermes transcrever a **sua voz** (microfone do app, áudios do Telegram), no `config.yaml`:
```yaml
stt:
  enabled: true
  provider: openai
  openai:
    base_url: http://127.0.0.1:8036/v1
    api_key: sem-chave
    model: parakeet-tdt-0.6b-v3
```

**OpenCode** (`opencode.json`)
```json
{ "mcp": { "stt-local": { "type": "remote", "url": "http://127.0.0.1:8036/mcp", "enabled": true } } }
```

**Claude Code**
```
claude mcp add --transport http stt-local http://127.0.0.1:8036/mcp
```

**Codex** (`~/.codex/config.toml`)
```toml
[mcp_servers.stt-local]
url = "http://127.0.0.1:8036/mcp"
```

Ferramentas do MCP: `transcrever_arquivo`, `transcrever_base64`, `status_do_stt` e `como_conectar` (o próprio agente pergunta como configurar outro programa).

## API (programadores)

```bash
curl -X POST http://127.0.0.1:8036/v1/audio/transcriptions -F "file=@audio.ogg" -F "response_format=json"
```
```python
from openai import OpenAI
cliente = OpenAI(base_url="http://127.0.0.1:8036/v1", api_key="sem-chave")
texto = cliente.audio.transcriptions.create(model="parakeet-tdt-0.6b-v3", file=open("audio.ogg", "rb")).text
```

- `response_format`: `json`, `text` ou `verbose_json` (trechos com tempo; com `timestamp_granularities[]=word`, cada palavra).
- Entrada: wav, mp3, ogg/opus (WhatsApp e Telegram), m4a, flac, webm, mp4, mkv, aac, wma. Até 500 MB e 2 h por arquivo.
- Sem multipart: `POST /v1/audio/transcriptions/base64` com `{"audio_base64": "...", "filename": "a.ogg"}`.
- Outras rotas: `GET /health`, `GET /v1/models`.

## Como funciona

```
🎤 áudio → detector de fala (Silero VAD) → Parakeet TDT 0.6B v3 (int8, ONNX Runtime, CPU) → 📝 texto
```

- **Detector de fala:** só os trechos com voz vão para o modelo. Silêncio, respiração e ruído voltam vazios, em vez de virar palavras inventadas. Custa ~2 ms por segundo de áudio.
- **Núcleos:** no automático usa 1/4 dos núcleos físicos (entre 1 e 8), deixando o resto do PC livre. Num Ryzen 7 5700X, 24 s de fala viram texto em ~1 s com 4 núcleos. Parado, ocupa ~0,75 GB de RAM e nada de CPU.
- **Download do modelo:** de uma revisão fixa no Hugging Face, com SHA-256 conferido arquivo a arquivo; continua de onde parou se a internet cair.

## Segurança

- Por padrão, **só este computador** acessa. A opção "Aceitar outros computadores" libera a rede local ou a VPN. **Não há senha: não exponha a porta na internet.**
- As rotas de configuração da tela recusam pedidos de outros sites abertos no navegador; o MCP também.
- `transcrever_arquivo` (ler um arquivo do disco) só atende quem chama da própria máquina.

## Arquivos

| | |
|---|---|
| `INICIAR.bat` | Abre com janela e com o navegador. |
| `INICIAR_OCULTO.cmd` | Abre sem janela (usado pelo "Iniciar com o Windows"); log em `dados\logs\stt.log`. |
| `PARAR.bat` | Desliga o serviço. |
| `dados\config.json` | Porta, rede, núcleos, detector e ditado. Quem escreve é a tela. |
| `modelo\` | O detector de fala (incluído) e o Parakeet (baixado pela tela). |

## Desenvolvimento

Este repositório tem o código; o pacote para baixar é gerado a partir dele.

```
EMPACOTAR.bat               → dist\stt-local + zip, sem o modelo (a tela baixa)
EMPACOTAR.bat --com-modelo  → idem, já com o Parakeet dentro
```

O empacotador precisa de um `runtime\` (Python 3.11 embutido com os pacotes de `requirements.txt`) e, no fim, procura dados pessoais (caminho de usuário, IPs da máquina, domínio da VPN, tokens): se achar algum, apaga o pacote e avisa.

## Créditos e licenças

Código: [MIT](LICENSE) © Dantas ([tech-dantas](https://github.com/tech-dantas)).
Modelo **Parakeet TDT 0.6B v3** © NVIDIA, licença CC-BY-4.0 (conversão ONNX de [istupakov](https://huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx)).
Detector **Silero VAD** (MIT). Demais componentes em [AVISOS-DE-TERCEIROS.md](AVISOS-DE-TERCEIROS.md).
