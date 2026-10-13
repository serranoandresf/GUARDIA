"""La copia del panel en la raíz (la que abre GitHub Pages) debe ser idéntica a web/ (la que entrega el servidor)."""
import json
import sys
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "scripts"))
import sincronizar_raiz as S  # noqa: E402


class PagesTests(unittest.TestCase):
    def test_root_copy_matches_web(self) -> None:
        self.assertEqual(S.distintos(), [], "ejecuta: python scripts/sincronizar_raiz.py")

    def test_every_asset_referenced_by_the_app_exists_in_the_root(self) -> None:
        manifest = json.loads((RAIZ / "manifest.webmanifest").read_text(encoding="utf-8"))
        for icon in manifest["icons"]:
            self.assertTrue((RAIZ / icon["src"]).exists(), icon["src"])
        head = (RAIZ / "index.html").read_text(encoding="utf-8").split("</head>")[0]
        for name in ("manifest.webmanifest", "icon.svg", "icon-192.png"):
            self.assertIn(name, head)
            self.assertTrue((RAIZ / name).exists())
        self.assertIn("register('sw.js')", (RAIZ / "index.html").read_text(encoding="utf-8"))

    def test_pages_does_not_run_jekyll_and_has_no_redirect(self) -> None:
        self.assertTrue((RAIZ / ".nojekyll").exists())
        self.assertNotIn('http-equiv="refresh"', (RAIZ / "index.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
