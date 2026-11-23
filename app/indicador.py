"""Indicador flutuante do Ditado: uma pílula na parte de baixo da tela, estilo rádio.

- Ouvindo:     ponto vermelho pulsando + barras que sobem e descem com a sua voz.
- Escrevendo:  barras azuis numa onda contínua, enquanto o texto é transcrito.
- Pronto:      ✓ verde por um instante (ou "Sem fala" quando não havia voz), e some.

Feito direto com a API do Windows (ctypes + GDI), sem pacote extra. A janela fica sempre por cima,
não aparece na barra de tarefas, deixa o clique passar e NUNCA pega o foco: o texto continua indo
para o programa onde o cursor está.
"""
from __future__ import annotations

import ctypes
import math
import threading
import time
from collections import deque
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

WS_POPUP = 0x80000000
WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_LAYERED = 0x00000008, 0x00000080, 0x00080000
WS_EX_TRANSPARENT, WS_EX_NOACTIVATE = 0x00000020, 0x08000000
WM_PAINT, WM_TIMER, WM_DESTROY, WM_ERASEBKGND = 0x000F, 0x0113, 0x0002, 0x0014
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
HWND_TOPMOST = wintypes.HWND(-1)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x1, 0x2, 0x10, 0x40
LWA_ALPHA = 0x2
SPI_GETWORKAREA = 0x0030
DT_LEFT, DT_VCENTER, DT_SINGLELINE = 0x0, 0x4, 0x20
SRCCOPY = 0x00CC0020

LARGURA_BASE, ALTURA_BASE = 292, 54   # em 100% de escala; multiplicado pela escala da tela
N_BARRAS = 18


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", wintypes.HDC), ("fErase", wintypes.BOOL), ("rcPaint", wintypes.RECT), ("fRestore", wintypes.BOOL),
                ("fIncUpdate", wintypes.BOOL), ("rgbReserved", ctypes.c_byte * 32)]


user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, wintypes.LPVOID]
user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.BeginPaint.restype = wintypes.HDC
user32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.InvalidateRect.argtypes = [wintypes.HWND, wintypes.LPVOID, wintypes.BOOL]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.COLORREF, ctypes.c_ubyte, wintypes.DWORD]
user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE, wintypes.BOOL]
user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH]
user32.DrawTextW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(wintypes.RECT), wintypes.UINT]
user32.GetDpiForSystem.restype = wintypes.UINT
user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, wintypes.LPVOID, wintypes.UINT]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
gdi32.CreateRoundRectRgn.restype = wintypes.HANDLE
gdi32.CreateEllipticRgn.argtypes = [ctypes.c_int] * 4
gdi32.CreateEllipticRgn.restype = wintypes.HANDLE
gdi32.FillRgn.argtypes = [wintypes.HDC, wintypes.HANDLE, wintypes.HBRUSH]
gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
gdi32.SelectObject.restype = wintypes.HANDLE
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HANDLE
gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
gdi32.CreateFontW.argtypes = [ctypes.c_int] * 5 + [wintypes.DWORD] * 8 + [wintypes.LPCWSTR]
gdi32.CreateFontW.restype = wintypes.HANDLE


def _rgb(r: int, g: int, b: int) -> int:
    return r | (g << 8) | (b << 16)


FUNDO = _rgb(17, 24, 39)
VERMELHO, AZUL, VERDE, CINZA, BRANCO = _rgb(239, 68, 68), _rgb(96, 147, 245), _rgb(74, 222, 128), _rgb(148, 163, 184), _rgb(241, 245, 249)


class Indicador:
    """Pílula flutuante. Chame de qualquer thread: ouvindo(), nivel(rms), escrevendo(), pronto(teve_texto), esconder()."""

    def __init__(self) -> None:
        self.estado = "oculto"          # oculto | ouvindo | escrevendo | pronto | vazio | erro
        self.ate = 0.0                  # estados "pronto/vazio/erro" somem sozinhos depois disso
        self.niveis: deque[float] = deque([0.0] * N_BARRAS, maxlen=N_BARRAS)
        self.ligado = True
        self._hwnd = None
        self._visivel = False
        self._t0 = time.time()
        self._thread: threading.Thread | None = None
        self._pronto = threading.Event()
        self._wndproc = WNDPROC(self._proc)   # referência viva (o ctypes não pode coletar)
        self._fonte = None
        self.e = 1.0

    # ── interface ──────────────────────────────────────────
    def ouvindo(self) -> None:
        self.niveis.extend([0.0] * N_BARRAS)
        self._mudar("ouvindo")

    def nivel(self, rms: float) -> None:
        db = 20 * math.log10(max(rms, 1e-6))
        self.niveis.append(min(1.0, max(0.0, (db + 52) / 40)))   # -52 dB = parado, -12 dB = alto

    def escrevendo(self) -> None:
        self._mudar("escrevendo")

    def pronto(self, teve_texto: bool) -> None:
        self._mudar("pronto" if teve_texto else "vazio", 0.9)

    def erro(self) -> None:
        self._mudar("erro", 1.6)

    def esconder(self) -> None:
        self.estado = "oculto"

    def _mudar(self, estado: str, duracao: float = 0.0) -> None:
        if not self.ligado:
            return
        self.estado, self.ate = estado, (time.time() + duracao if duracao else 0.0)
        self._garantir_janela()

    # ── janela ─────────────────────────────────────────────
    def _garantir_janela(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._pronto.clear()
        self._thread = threading.Thread(target=self._laco, name="indicador", daemon=True)
        self._thread.start()
        self._pronto.wait(2)

    def _laco(self) -> None:
        try:
            user32.SetThreadDpiAwarenessContext(wintypes.HANDLE(-4))   # nítido em telas com escala
        except Exception:  # noqa: BLE001
            pass
        try:
            self.e = user32.GetDpiForSystem() / 96
        except Exception:  # noqa: BLE001
            self.e = 1.0
        hinst = kernel32.GetModuleHandleW(None)
        classe = "STTLocalIndicador"
        wc = WNDCLASSW(lpfnWndProc=self._wndproc, hInstance=hinst, lpszClassName=classe)
        user32.RegisterClassW(ctypes.byref(wc))
        area = wintypes.RECT()
        user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(area), 0)
        LARGURA, ALTURA = self._px(LARGURA_BASE), self._px(ALTURA_BASE)
        x = area.left + (area.right - area.left - LARGURA) // 2
        y = area.bottom - ALTURA - self._px(28)
        estilo_ex = WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
        self._hwnd = user32.CreateWindowExW(estilo_ex, classe, "STT Local", WS_POPUP, x, y, LARGURA, ALTURA, None, None, hinst, None)
        user32.SetLayeredWindowAttributes(self._hwnd, 0, 238, LWA_ALPHA)
        user32.SetWindowRgn(self._hwnd, gdi32.CreateRoundRectRgn(0, 0, LARGURA + 1, ALTURA + 1, ALTURA, ALTURA), True)
        self._fonte = gdi32.CreateFontW(-self._px(15), 0, 0, 0, 600, 0, 0, 0, 0, 0, 0, 5, 0, "Segoe UI")
        user32.SetTimer(self._hwnd, 1, 33, None)                       # ~30 quadros por segundo
        self._pronto.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TIMER:
            if self.ate and time.time() > self.ate and self.estado in ("pronto", "vazio", "erro"):
                self.estado = "oculto"
            quer = self.estado != "oculto"
            if quer and not self._visivel:
                user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
                user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
                self._visivel = True
            elif not quer and self._visivel:
                user32.ShowWindow(hwnd, SW_HIDE)
                self._visivel = False
            if self._visivel:
                user32.InvalidateRect(hwnd, None, False)
            return 0
        if msg == WM_ERASEBKGND:
            return 1
        if msg == WM_PAINT:
            ps = PAINTSTRUCT()
            hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
            try:
                self._desenhar(hdc)
            finally:
                user32.EndPaint(hwnd, ctypes.byref(ps))
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _px(self, v: float) -> int:
        return int(round(v * self.e))

    def _desenhar(self, hdc_tela) -> None:
        LARGURA, ALTURA, px = self._px(LARGURA_BASE), self._px(ALTURA_BASE), self._px
        hdc = gdi32.CreateCompatibleDC(hdc_tela)
        bmp = gdi32.CreateCompatibleBitmap(hdc_tela, LARGURA, ALTURA)
        antigo = gdi32.SelectObject(hdc, bmp)
        objetos = []

        def pincel(cor):
            b = gdi32.CreateSolidBrush(cor)
            objetos.append(b)
            return b

        def bolinha(cx, cy, r, cor):
            rg = gdi32.CreateEllipticRgn(cx - r, cy - r, cx + r + 1, cy + r + 1)
            gdi32.FillRgn(hdc, rg, pincel(cor))
            gdi32.DeleteObject(rg)

        def barra(x, altura, cor):
            meio = ALTURA // 2
            altura = max(px(4), int(altura))
            rg = gdi32.CreateRoundRectRgn(x, meio - altura // 2, x + px(5), meio + altura // 2 + 1, px(5), px(5))
            gdi32.FillRgn(hdc, rg, pincel(cor))
            gdi32.DeleteObject(rg)

        user32.FillRect(hdc, ctypes.byref(wintypes.RECT(0, 0, LARGURA, ALTURA)), pincel(FUNDO))
        t = time.time() - self._t0
        estado = self.estado
        x0, maxh, passo = px(44), ALTURA - px(20), px(7)
        cx, cy = px(24), ALTURA // 2
        if estado == "ouvindo":
            bolinha(cx, cy, px(6 + 1.5 * math.sin(t * 7)), VERMELHO)
            for i, v in enumerate(list(self.niveis)):
                barra(x0 + i * passo, px(4) + v * maxh * (0.75 + 0.25 * math.sin(t * 9 + i)), BRANCO if v > 0.08 else CINZA)
            texto, cor = "Ouvindo", BRANCO
        elif estado == "escrevendo":
            bolinha(cx, cy, px(6), AZUL)
            for i in range(N_BARRAS):
                barra(x0 + i * passo, px(5) + (0.5 + 0.5 * math.sin(t * 8 - i * 0.6)) * maxh * 0.7, AZUL)
            texto, cor = "Escrevendo", AZUL
        elif estado == "pronto":
            bolinha(cx, cy, px(7), VERDE)
            for i in range(N_BARRAS):
                barra(x0 + i * passo, px(4), VERDE)
            texto, cor = "Pronto ✓", VERDE
        elif estado == "vazio":
            bolinha(cx, cy, px(6), CINZA)
            for i in range(N_BARRAS):
                barra(x0 + i * passo, px(4), CINZA)
            texto, cor = "Sem fala", CINZA
        else:
            bolinha(cx, cy, px(6), VERMELHO)
            texto, cor = "Erro", VERMELHO

        gdi32.SetBkMode(hdc, 1)
        gdi32.SetTextColor(hdc, cor)
        fonte_antiga = gdi32.SelectObject(hdc, self._fonte)
        rect = wintypes.RECT(x0 + N_BARRAS * passo + px(10), 0, LARGURA - px(8), ALTURA)
        user32.DrawTextW(hdc, texto, -1, ctypes.byref(rect), DT_LEFT | DT_VCENTER | DT_SINGLELINE)
        gdi32.SelectObject(hdc, fonte_antiga)

        gdi32.BitBlt(hdc_tela, 0, 0, LARGURA, ALTURA, hdc, 0, 0, SRCCOPY)
        gdi32.SelectObject(hdc, antigo)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(hdc)
        for o in objetos:
            gdi32.DeleteObject(o)


if __name__ == "__main__":   # demonstração: python indicador.py
    import random
    ind = Indicador()
    ind.ouvindo()
    for _ in range(90):
        ind.nivel(random.choice([0.002, 0.02, 0.06, 0.12]))
        time.sleep(0.033)
    ind.escrevendo()
    time.sleep(1.5)
    ind.pronto(True)
    time.sleep(1.2)
