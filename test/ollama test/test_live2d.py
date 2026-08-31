"""
单元测试：Live2D 桌面宠物后端（模型扫描 / 列表）
================================================
- _scan_models 按目录发现含 model.json 的模型
- 模型 id / url / name 组装正确
"""
import json

import pytest


def _write_model(root, rel_dir, name="", version=None):
    d = root / rel_dir
    d.mkdir(parents=True, exist_ok=True)
    data = {}
    if name:
        data["name"] = name
    data["version"] = version or "3"
    (d / "model.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_scan_models_finds_model(tmp_path, monkeypatch):
    import api.live2d as L
    root = tmp_path / "live2d"
    _write_model(root, "Toyama Kasumi/001_event", name="户山香澄")
    monkeypatch.setattr(L, "LIVE2D_MODEL_ROOT", str(root))

    models = L._scan_models()
    assert len(models) == 1
    m = models[0]
    assert m["id"] == "Toyama Kasumi/001_event"
    assert m["name"] == "户山香澄"
    assert m["model_url"].startswith("/live2d-models/")
    assert m["model_url"].endswith("model.json")


def test_scan_models_empty_dir(tmp_path, monkeypatch):
    import api.live2d as L
    root = tmp_path / "empty"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(L, "LIVE2D_MODEL_ROOT", str(root))
    assert L._scan_models() == []


def test_scan_models_multiple_and_default(tmp_path, monkeypatch):
    import api.live2d as L
    root = tmp_path / "live2d"
    _write_model(root, "ModelA", name="模型A")
    _write_model(root, "ModelB", name="模型B")
    monkeypatch.setattr(L, "LIVE2D_MODEL_ROOT", str(root))

    models = L._scan_models()
    assert len(models) == 2

    monkeypatch.setattr(L, "LIVE2D_DEFAULT_MODEL", "ModelB")
    assert L._default_model() == "ModelB"

    monkeypatch.setattr(L, "LIVE2D_DEFAULT_MODEL", "")
    assert L._default_model() in ("ModelA", "ModelB")  # 无配置则取第一个
