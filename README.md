# STT Portátil

Transcrição de áudio (fala → texto) que roda no **processador**, sem placa de vídeo e sem instalar nada. Usa o modelo **Parakeet TDT 0.6B v3** (NVIDIA, licença CC-BY-4.0), que entende português e mais 24 línguas europeias.

## Usar

1. Extraia a pasta onde quiser.
2. Dê dois cliques em **`INICIAR.bat`**. O navegador abre a tela em `http://127.0.0.1:8036/`.
3. Fechar a janela preta desliga o serviço.

Na tela você vê:
- **Endpoints** que os seus programas podem chamar, com exemplo.
- **Uso agora:** CPU do serviço e da máquina, RAM e núcleos em uso.
- **Transcrições:** quantas foram feitas, quanto áudio e a velocidade média.
- **Configuração:** núcleos, porta, acesso pela rede e iniciar com o Windows.
- **Testar:** grave uma fala ou escolha um arquivo.

## Endpoint

O contrato é o mesmo da API de transcrição da OpenAI:

```bash
curl -X POST http://127.0.0.1:8036/v1/audio/transcriptions \
  -F "file=@audio.ogg" -F "response_format=json"
```

- **`response_format`:** `json`, `text` ou `verbose_json`, que traz trechos com tempo. Para ter o tempo de cada palavra, adicione `timestamp_granularities[]=word`.
- **Formatos de entrada:** wav, mp3, ogg/opus (áudio do WhatsApp e do Telegram), m4a, flac, webm, mp4, mkv, aac, wma.
- **Sem multipart:** `POST /v1/audio/transcriptions/base64`, com o JSON `{"audio_base64": "...", "filename": "a.ogg"}`.
- **Clientes OpenAI:** use `base_url = http://127.0.0.1:8036/v1` e qualquer `api_key`.
- **Outras rotas:** `GET /health` e `GET /v1/models`.

## Núcleos do processador

No automático, o serviço usa **1/4 dos núcleos físicos, entre 1 e 8**. O resto do processador fica livre para os outros programas.

| Núcleos físicos | 1 a 5 | 6 a 8 | 16 | 32 ou mais |
|---|---|---|---|---|
| Usa | 1 | 2 | 4 | 8 |

Acima de 8 núcleos, a velocidade quase não melhora. Num Ryzen 7 5700X, uma fala de 24 s foi transcrita assim:

| Núcleos | Tempo |
|---|---|
| 1 | 2,6 s |
| 2 | 1,6 s |
| 4 | 1,0 s |
| 8 | 0,86 s |

O serviço só usa CPU enquanto transcreve. Parado, ocupa uns 0,75 GB de RAM e nada de CPU. Para trocar o número de núcleos, use a tela: vale na hora, sem reiniciar.

## Trava de silêncio

Áudio sem fala (silêncio, ruído da sala, respiração, clique do microfone) volta com o texto **vazio**. Sem a trava, o modelo às vezes inventava uma palavra solta, como "Yeah.", para um clipe só de ruído.

Antes de transcrever, um detector de fala (Silero VAD, o mesmo que o Whisper usa) marca onde tem voz. Só esses trechos vão para o modelo. Isso leva uns 2 ms por segundo de áudio. Os silêncios longos entre as falas ficam de fora, e os tempos de cada trecho continuam certos.

Vem ligada. Para desligar, use a tela, em Configuração; vale na hora. Na tela, em Transcrições, aparece quantos áudios voltaram vazios por falta de fala.

## Rede

Por padrão, **só esta máquina** acessa o serviço. Marcando "Aceitar conexões de outros computadores", outros PCs da sua rede local ou da tailnet podem chamar o endpoint, e a tela mostra os endereços. Na primeira vez, o Windows pode perguntar sobre o firewall; libere só para redes privadas.

Não há senha. Não exponha a porta na internet.

## Arquivos

- `INICIAR.bat`: abre com janela e com o navegador.
- `INICIAR_OCULTO.cmd`: abre sem janela. É o que o "Iniciar com o Windows" usa, e o log vai para `dados\logs\stt.log`.
- `PARAR.bat`: desliga o serviço que estiver rodando.
- `dados\config.json`: porta, rede, núcleos e trava de silêncio. Quem escreve nele é a tela.
- `runtime\`: Python 3.11 com onnxruntime (CPU), onnx-asr, FastAPI e PyAV.
- `modelo\`: os pesos do Parakeet int8 (640 MB) e o detector de fala `silero_vad_v6.onnx` (1,2 MB, licença MIT).

Requisitos: Windows 10/11 de 64 bits e uns 2 GB de RAM livres.
