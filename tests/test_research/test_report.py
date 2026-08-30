import json

from sngw_trader.research.report import run_dir, write_reports


def test_run_dir_structure(tmp_path):
    d = run_dir("EMACross", base=tmp_path)
    assert d.parent.name == "EMACross"
    assert d.parent.parent == tmp_path
    assert d.is_dir()


def test_write_reports(tmp_path):
    d = run_dir("EMACross", base=tmp_path)
    write_reports(d, {"windows": []}, {"oos": None}, {"n_windows": 1})
    names = {p.name for p in d.iterdir()}
    assert names == {"wf_report.json", "mc_report.json", "summary.json"}
    assert json.loads((d / "summary.json").read_text()) == {"n_windows": 1}
    assert json.loads((d / "wf_report.json").read_text()) == {"windows": []}