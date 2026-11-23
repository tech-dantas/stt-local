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

## Licença do Silero VAD (modelo/silero_vad_v6.onnx)

```
MIT License

Copyright (c) 2020-present Silero Team

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## FFmpeg (dentro do PyAV)

O pacote inclui as bibliotecas do FFmpeg que vêm com o PyAV, sob a LGPL. Elas ficam como DLLs separadas em `runtime\Lib\site-packages\av.libs` e podem ser trocadas por outras versões compatíveis. Código-fonte: https://ffmpeg.org e https://github.com/PyAV-Org/PyAV.
