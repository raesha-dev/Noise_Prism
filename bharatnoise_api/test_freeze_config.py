import json

import pytest

from bharatnoise_api.scripts import freeze_config, run_frozen_test


def _workspace(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "config" / "router.json").write_text(
        json.dumps({"fitted": True, "rules": [{"min_snr_db": -100, "variant": "C0"}]}),
        encoding="utf-8",
    )
    (tmp_path / "config" / "suppress.json").write_text(
        json.dumps({"fitted": False, "wiener_gain_floor": 0.18}), encoding="utf-8"
    )
    (tmp_path / "scripts" / "run_baseline.py").write_text("print('baseline')\n", encoding="utf-8")


def test_frozen_hash_detects_code_or_config_changes(tmp_path, monkeypatch):
    _workspace(tmp_path)
    monkeypatch.setattr(freeze_config, "ROOT", tmp_path)
    frozen_path = tmp_path / "config" / "frozen.json"
    payload = freeze_config.freeze(None, frozen_path)
    assert freeze_config.verify_frozen_config(frozen_path)["code_config_sha256"] == payload["code_config_sha256"]
    (tmp_path / "scripts" / "run_baseline.py").write_text("print('changed')\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hash does not match"):
        freeze_config.verify_frozen_config(frozen_path)


def test_freeze_rejects_placeholder_suppression_when_selected(tmp_path, monkeypatch):
    _workspace(tmp_path)
    monkeypatch.setattr(freeze_config, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="Suppressor parameters must be fitted"):
        freeze_config.freeze(0.1, tmp_path / "config" / "frozen.json")


def test_baseline_only_configuration_can_be_frozen_without_claiming_router_fit(tmp_path, monkeypatch):
    _workspace(tmp_path)
    router_path = tmp_path / "config" / "router.json"
    router_path.write_text(json.dumps({
        "fitted": False,
        "baseline_only": True,
        "routing_estimator_validated": False,
        "rules": [{"min_snr_db": -100, "variant": "C0"}],
    }), encoding="utf-8")
    monkeypatch.setattr(freeze_config, "ROOT", tmp_path)

    frozen_path = tmp_path / "config" / "frozen.json"
    payload = freeze_config.freeze(None, frozen_path)

    assert payload["fitted"] is True
    assert payload["router"]["fitted"] is False
    assert "baseline-only" in payload["note"]
    assert freeze_config.verify_frozen_config(frozen_path)["router"]["fitted"] is False


def test_baseline_only_freeze_rejects_non_c0_rules(tmp_path, monkeypatch):
    _workspace(tmp_path)
    router_path = tmp_path / "config" / "router.json"
    router_path.write_text(json.dumps({
        "fitted": False,
        "baseline_only": True,
        "rules": [{"min_snr_db": -100, "variant": "C1"}],
    }), encoding="utf-8")
    monkeypatch.setattr(freeze_config, "ROOT", tmp_path)

    with pytest.raises(ValueError, match="fitted on development data"):
        freeze_config.freeze(None, tmp_path / "config" / "frozen.json")


def test_held_out_state_file_is_exclusive(tmp_path):
    path = tmp_path / "state.json"
    run_frozen_test._write_state(path, {"status": "started"}, exclusive=True)
    with pytest.raises(FileExistsError):
        run_frozen_test._write_state(path, {"status": "started"}, exclusive=True)
