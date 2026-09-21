"""人工项没填时**不能**当 0 算 —— 会打出看上去像分数的假数。

这是打分器里最容易出事的地方：A 维度三项全靠人工，缺项按 0 算的话，
一份还没评的工作表能打出「A=0.0、总分 25.0、AtomCode 是 opencode 的 100.0%」，
而且 D 还会被「A<12 记 0」的闸连坐清零。三个数字没有一个是真的。
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCORE = REPO / "tools" / "eval" / "score.py"

FULL_AUTO = {"C1": {"score": 5, "max": 5, "why": ""},
             "C4": {"score": 5, "max": 5, "why": ""},
             "B2": {"score": 8.0, "max": 8, "why": ""},
             "B3": {"score": 7.0, "max": 7, "why": ""},
             "D1": {"score": 13.0, "max": 13, "why": ""},
             "D2": {"score": 12.0, "max": 12, "why": ""}}
MANUAL_FULL = {"A1": {"score": 10, "max": 10, "why": "x"},
               "A2": {"score": 8, "max": 8, "why": "x"},
               "A3": {"score": 7, "max": 7, "why": "x"},
               "B1": {"score": 10, "max": 10, "why": "x"},
               "C2": {"score": 5, "max": 5, "why": "x"},
               "C3": {"score": 5, "max": 5, "why": "x"},
               "C5": {"score": 5, "max": 5, "why": "x"}}
MANUAL_EMPTY = {k: {"score": None, "max": v["max"], "why": ""}
                for k, v in MANUAL_FULL.items()}


def _run(tmp_path, manual_by_side):
    scores = {"rubric": {}, "d_baseline": {}, "runs": {}}
    for side, manual in manual_by_side.items():
        scores["runs"][f"q1-{side}-r1"] = {
            "question": "q1", "side": side, "file": "x.json",
            "facts": {}, "auto": dict(FULL_AUTO),
            "manual": {k: dict(v) for k, v in manual.items()},
        }
    f = tmp_path / "scores.json"
    f.write_text(json.dumps(scores, ensure_ascii=False), encoding="utf-8")
    p = subprocess.run([sys.executable, str(SCORE), "report", "--scores", str(f)],
                       capture_output=True, text=True)
    summary = json.loads((tmp_path / "scores-summary.json").read_text(encoding="utf-8"))
    return p, summary


def test_人工项没填时整次不出分(tmp_path):
    p, s = _run(tmp_path, {"atomcode": MANUAL_EMPTY, "opencode": MANUAL_EMPTY})
    row = s["rows"][0]
    assert row["total"] is None and row["A"] is None, "缺项时不该给出任何分"
    assert row["missing"], "缺哪几项要如实记下来"
    for side in ("atomcode", "opencode"):
        assert s["summary"][side]["total"] is None
        assert s["summary"][side]["n_scored"] == 0
        assert s["summary"][side]["n_total"] == 1
    assert all(v is None for v in s["ratios"].values()), "分母分子都没有，比值必须是 —"
    assert p.returncode == 3, "有缺项要用非 0 退出码，好让自动化分得清"


def test_填完了才出分(tmp_path):
    p, s = _run(tmp_path, {"atomcode": MANUAL_FULL, "opencode": MANUAL_FULL})
    row = s["rows"][0]
    assert row["A"] == 25.0 and row["B"] == 25.0 and row["C"] == 25.0 and row["D"] == 25.0
    assert row["total"] == 100.0
    assert s["ratios"]["total"] == 100.0
    assert p.returncode == 0


def test_只有一边评完时另一边显示破折号(tmp_path):
    p, s = _run(tmp_path, {"atomcode": MANUAL_FULL, "opencode": MANUAL_EMPTY})
    assert s["summary"]["atomcode"]["total"] == 100.0
    assert s["summary"]["opencode"]["total"] is None
    assert s["ratios"]["total"] is None, "基线没评完就不能算比值"
    assert p.returncode == 3
