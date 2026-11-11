import os
import subprocess
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

RAIZ = Path(__file__).resolve().parents[1]


def test_app_com_demo(tmp_path, monkeypatch):
    banco = tmp_path / "t.db"
    monkeypatch.setenv("BANCO", str(banco))
    env = {**os.environ, "BANCO": str(banco)}
    subprocess.run([sys.executable, "-m", "painel.coletar", "--demo"], cwd=RAIZ, env=env, check=True)
    import importlib
    import painel.config
    importlib.reload(painel.config)
    at = AppTest.from_file(str(RAIZ / "app.py"), default_timeout=60).run()
    assert not at.exception, at.exception
    assert len(at.tabs) >= 2
    assert at.metric[0].value == "300"
    rotulos = [m.label for m in at.metric]
    assert "Dependem da MEH" in rotulos and "Rápidas" in rotulos


def test_app_sem_dados(tmp_path, monkeypatch):
    monkeypatch.setenv("BANCO", str(tmp_path / "vazio.db"))
    import importlib
    import painel.config
    importlib.reload(painel.config)
    at = AppTest.from_file(str(RAIZ / "app.py"), default_timeout=60).run()
    assert not at.exception
    assert any("Sem dados ainda" in w.value for w in at.warning)
