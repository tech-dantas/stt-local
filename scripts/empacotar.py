"""Gera o pacote portátil do STT Local a partir desta pasta (o projeto principal).

    EMPACOTAR.bat                 pasta dist\\stt-local + zip, SEM o modelo (a tela baixa)
    EMPACOTAR.bat --com-modelo    idem, já com o Parakeet dentro (zip ~650 MB maior)
    EMPACOTAR.bat --sem-zip       só a pasta

O que entra: app\\, os .bat/.cmd, README, licenças, modelo\\silero_vad_v6.onnx e o runtime\\
(Python embutido). O que NUNCA entra: dados\\ (config, logs), runtime\\Scripts (lançadores
com o caminho desta máquina), caches .pyc e backups.

No fim, uma varredura procura dado pessoal (caminho de usuário, IP da tailnet, nome da
máquina, tokens). Se achar, o pacote é apagado e o script falha.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import socket
import sys
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DIST = RAIZ / "dist"
NOME = "stt-local"

ARQUIVOS_RAIZ = ["INICIAR.bat", "INICIAR_OCULTO.cmd", "PARAR.bat", "README.md", "LICENSE", "AVISOS-DE-TERCEIROS.md"]
IGNORAR = shutil.ignore_patterns("__pycache__", "*.pyc", "*.bak*", "*.antes-*", "*.tmp")
EXT_TEXTO = {".py", ".html", ".md", ".txt", ".json", ".bat", ".cmd", ".ps1", ".cfg", ".toml", ".ini", ".pth", ".vbs", ".js", ".css"}


def versao() -> str:
    texto = (RAIZ / "app" / "servidor.py").read_text(encoding="utf-8")
    return re.search(r'^VERSAO = "([^"]+)"', texto, re.M).group(1)


def _ips_desta_maquina() -> set[str]:
    ips: set[str] = set()
    try:
        import psutil
        for enderecos in psutil.net_if_addrs().values():
            for e in enderecos:
                if e.family == socket.AF_INET and not e.address.startswith(("127.", "169.254.", "0.")):
                    ips.add(e.address)
    except Exception:  # noqa: BLE001
        pass
    return ips


def padroes_pessoais() -> list[tuple[str, re.Pattern[str]]]:
    """O que identifica ESTA máquina e ESTE usuário. Genérico (ex.: 'C:\\Users\\runneradmin'
    de quem compilou o numpy, a faixa 100.64.0.0 no ipaddress.py) não é dado pessoal."""
    usuario = os.environ.get("USERNAME", "")
    lista = [
        ("domínio da tailnet", re.compile(r"\b[\w-]+\.ts\.net\b", re.I)),
        ("token", re.compile(r"\b(?:gh[opsu]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|hf_[A-Za-z0-9]{20,})\b")),
    ]
    if len(usuario) >= 3:
        lista.append(("pasta do usuário", re.compile(rf"[A-Za-z]:[\\/]+Users[\\/]+{re.escape(usuario)}\b", re.I)))
        lista.append(("nome do usuário", re.compile(rf"(?<![\w.-]){re.escape(usuario)}(?![\w-])", re.I)))
    for nome in {socket.gethostname(), os.environ.get("COMPUTERNAME", "")}:
        # Só nomes que identificam (DESKTOP-ABC1234); um "desktop" puro é palavra comum.
        if len(nome) >= 6 and re.search(r"[-\d]", nome):
            lista.append(("nome da máquina", re.compile(rf"\b{re.escape(nome)}\b", re.I)))
    for ip in _ips_desta_maquina():
        lista.append(("IP desta máquina", re.compile(rf"(?<![\d.]){re.escape(ip)}(?![\d.])")))
    return lista


def varrer(pasta: Path) -> list[str]:
    achados: list[str] = []
    padroes = padroes_pessoais()
    for arq in pasta.rglob("*"):
        if not arq.is_file() or arq.suffix.lower() not in EXT_TEXTO or arq.stat().st_size > 5 * 2**20:
            continue
        try:
            texto = arq.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for nome, padrao in padroes:
            m = padrao.search(texto)
            if m:
                linha = texto.count("\n", 0, m.start()) + 1
                achados.append(f"{arq.relative_to(pasta)}:{linha}  [{nome}]  {m.group(0)[:60]}")
    return achados


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--com-modelo", action="store_true", help="inclui o Parakeet (senão a tela baixa)")
    ap.add_argument("--sem-zip", action="store_true", help="gera só a pasta")
    args = ap.parse_args()

    v = versao()
    runtime = RAIZ / "runtime"
    if not (runtime / "python.exe").exists():
        print("Falta runtime\\python.exe (o Python embutido). Veja o README, seção Desenvolvimento.")
        return 1
    destino = DIST / NOME
    if destino.exists():
        shutil.rmtree(destino)
    destino.mkdir(parents=True)
    print(f"Gerando STT Local {v} em {destino}")

    shutil.copytree(RAIZ / "app", destino / "app", ignore=IGNORAR)
    for nome in ARQUIVOS_RAIZ:
        shutil.copy2(RAIZ / nome, destino / nome)
    (destino / "modelo").mkdir()
    shutil.copy2(RAIZ / "modelo" / "silero_vad_v6.onnx", destino / "modelo")
    shutil.copytree(runtime, destino / "runtime", ignore=shutil.ignore_patterns("Scripts", "__pycache__", "*.pyc"))
    if args.com_modelo:
        origem = RAIZ / "modelo" / "parakeet-tdt-0.6b-v3-int8"
        if not (origem / "encoder-model.int8.onnx").exists():
            print("--com-modelo: o modelo não está em modelo\\parakeet-tdt-0.6b-v3-int8. Baixe pela tela antes.")
            return 1
        shutil.copytree(origem, destino / "modelo" / origem.name, ignore=IGNORAR)

    achados = varrer(destino)
    if achados:
        print("\nDADO PESSOAL ENCONTRADO — pacote apagado:")
        for a in achados[:40]:
            print("  ", a)
        shutil.rmtree(destino)
        return 2
    print("Varredura de dados pessoais: limpo.")

    if not args.sem_zip:
        sufixo = "-com-modelo" if args.com_modelo else ""
        zip_path = DIST / f"{NOME}-v{v}-windows{sufixo}.zip"
        zip_path.unlink(missing_ok=True)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for arq in sorted(destino.rglob("*")):
                if arq.is_file():
                    z.write(arq, Path(NOME) / arq.relative_to(destino))
        print(f"Zip: {zip_path} ({zip_path.stat().st_size / 2**20:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
