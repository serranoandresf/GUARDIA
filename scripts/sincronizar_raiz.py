"""Copia el panel de web/ a la raíz del repositorio para que GitHub Pages lo abra en https://USUARIO.github.io/REPO/.

Uso: python scripts/sincronizar_raiz.py           (copia)
     python scripts/sincronizar_raiz.py --check   (solo comprueba; sale con 1 si hay diferencias)
"""
import shutil
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ARCHIVOS = ("index.html", "manifest.webmanifest", "sw.js", "icon.svg", "icon-192.png", "icon-512.png")


def distintos() -> list[str]:
    return [n for n in ARCHIVOS if not (RAIZ / n).exists() or (RAIZ / n).read_bytes() != (RAIZ / "web" / n).read_bytes()]


def main(argv: list[str]) -> int:
    pendientes = distintos()
    if "--check" in argv:
        print("Raíz y web/ coinciden." if not pendientes else f"Diferencias en: {', '.join(pendientes)}")
        return 1 if pendientes else 0
    for nombre in pendientes:
        shutil.copyfile(RAIZ / "web" / nombre, RAIZ / nombre)
    print(f"Copiados: {', '.join(pendientes) or 'nada (ya estaba al día)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
