# Avisos de terceiros

O código do STT Local é MIT (veja `LICENSE`). Ele usa e distribui partes de terceiros:

| Componente | Autor | Licença | Como entra |
|---|---|---|---|
| Parakeet TDT 0.6B v3 | NVIDIA | CC-BY-4.0 | Baixado pela tela, da conversão ONNX de [istupakov/parakeet-tdt-0.6b-v3-onnx](https://huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx) (modelo original: [nvidia/parakeet-tdt-0.6b-v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)) |
| Silero VAD v6 (`modelo/silero_vad_v6.onnx`) | Silero Team | MIT | Incluído no repositório |
| onnx-asr | istupakov | MIT | No runtime do pacote |
| ONNX Runtime | Microsoft | MIT | No runtime do pacote |
| FastAPI, Starlette, Uvicorn, Pydantic | seus autores | MIT / BSD | No runtime do pacote |
| PyAV / FFmpeg | PyAV e FFmpeg | BSD / LGPL | No runtime do pacote |
| NumPy, psutil | seus autores | BSD | No runtime do pacote |
| sounddevice / PortAudio | Matthias Geier / PortAudio | MIT | No runtime do pacote (microfone do Ditado) |
| cffi, pycparser | seus autores | MIT / BSD | No runtime do pacote |
| Python 3.11 (embutido) | Python Software Foundation | PSF | No runtime do pacote |

Ao usar o modelo Parakeet, mantenha o crédito à NVIDIA (exigência da CC-BY-4.0).
