"""Task 1: Model-aware budget — context window resolution tests."""

import importlib


def test_budget_uses_model_record_context_length(monkeypatch):
    from novi.runtime.context_budget import ContextBudgetManager
    from novi.configuration.model_records import ModelRecord

    class FakeReg:
        def get(self, name):
            return ModelRecord(name=name, context_length=32768)

    # Patch get_global_registry to return FakeReg
    import novi.models.registry as reg_mod

    def fake_get_global_registry():
        return FakeReg()

    # Ensure get_global_registry exists or patch it
    monkeypatch.setattr(reg_mod, "get_global_registry", fake_get_global_registry, raising=False)

    # Also patch load_model_record path to not interfere — make it return None
    import novi.configuration.model_records as mr_mod
    monkeypatch.setattr(mr_mod, "load_model_record", lambda name: None, raising=False)

    window, source = ContextBudgetManager.get_context_window("qwen3:27b")
    assert window == 32768
    assert source == "model_record"


def test_budget_fallback_default_unknown():
    from novi.runtime.context_budget import ContextBudgetManager

    window, source = ContextBudgetManager.get_context_window("unknown-model-xyz")
    assert window == 8192
    assert source == "fallback_default"


def test_budget_fallback_small_7b():
    from novi.runtime.context_budget import ContextBudgetManager

    window, source = ContextBudgetManager.get_context_window("llama3:7b")
    assert window == 4096
    assert source == "fallback_small"


def test_budget_fallback_small_mini():
    from novi.runtime.context_budget import ContextBudgetManager

    window, source = ContextBudgetManager.get_context_window("my-mini-model")
    assert window == 4096
    assert source == "fallback_small"


def test_budget_none_model_name():
    from novi.runtime.context_budget import ContextBudgetManager

    window, source = ContextBudgetManager.get_context_window(None)
    assert window == 8192
    assert source == "fallback_default"


def test_budget_load_model_record_path(monkeypatch):
    """Second resolution path via load_model_record when registry has no record."""
    from novi.runtime.context_budget import ContextBudgetManager
    from novi.configuration.model_records import ModelRecord
    import novi.models.registry as reg_mod
    import novi.configuration.model_records as mr_mod

    # Registry returns None
    class EmptyReg:
        def get(self, name):
            return None

    monkeypatch.setattr(reg_mod, "get_global_registry", lambda: EmptyReg(), raising=False)
    monkeypatch.setattr(mr_mod, "load_model_record", lambda name: ModelRecord(name=name, context_length=16384), raising=False)

    window, source = ContextBudgetManager.get_context_window("some-model:13b")
    assert window == 16384
    assert source == "model_record"


def test_budget_never_fabricates_none_stays_none(monkeypatch):
    """When record has None context_length, should fallback not fabricate."""
    from novi.runtime.context_budget import ContextBudgetManager
    from novi.configuration.model_records import ModelRecord
    import novi.models.registry as reg_mod
    import novi.configuration.model_records as mr_mod

    class NoneReg:
        def get(self, name):
            return ModelRecord(name=name, context_length=None)

    monkeypatch.setattr(reg_mod, "get_global_registry", lambda: NoneReg(), raising=False)
    monkeypatch.setattr(mr_mod, "load_model_record", lambda name: ModelRecord(name=name, context_length=None), raising=False)

    window, source = ContextBudgetManager.get_context_window("qwen3:27b")
    # Should not be model_record since context_length is None -> fallback
    assert source in ("fallback_small", "fallback_default")
    assert window in (4096, 8192)


def test_compute_uses_model_record_window(monkeypatch):
    from novi.runtime.context_budget import ContextBudgetManager
    from novi.configuration.model_records import ModelRecord
    import novi.models.registry as reg_mod
    import novi.configuration.model_records as mr_mod

    class FakeReg:
        def get(self, name):
            return ModelRecord(name=name, context_length=32768)

    monkeypatch.setattr(reg_mod, "get_global_registry", lambda: FakeReg(), raising=False)
    monkeypatch.setattr(mr_mod, "load_model_record", lambda name: None, raising=False)

    bd = ContextBudgetManager.compute(model_name="qwen3:27b")
    assert bd.context_window == 32768
    assert bd.source == "model_record"
    assert bd.available >= 0


def test_context_length_from_show_no_fabrication():
    from novi.configuration.runtime_inventory import _context_length_from_show

    assert _context_length_from_show({}) is None
    assert _context_length_from_show({"model_info": {}}) is None
    assert _context_length_from_show({"model_info": {"llama.context_length": 8192}}) == 8192
    assert _context_length_from_show({"model_info": {"model.context_length": 4096}}) == 4096
    # bool must not be treated as int
    assert _context_length_from_show({"model_info": {"llama.context_length": True}}) is None
