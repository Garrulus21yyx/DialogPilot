import asyncio
import json
from pathlib import Path

import pytest

from core.skill_loader import SkillManager
from infrastructure.target_skill_tools import skill_reader


def package(root, name="comparison", agents="[product_technical]", body="MAIN_ONLY"):
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Compare eligible variants\nagents: {agents}\n---\n{body}")
    (directory / "references").mkdir(exist_ok=True)
    (directory / "references" / "detail.md").write_text("REFERENCE_ONLY")
    return directory


def test_package_metadata_is_lazy_and_resources_are_not_skills(tmp_path):
    package(tmp_path)
    (tmp_path / "unrelated.md").write_text("not a package")
    manager = SkillManager(str(tmp_path))
    skill, = manager.load()
    assert len(manager.skills) == 1
    assert manager.for_agent("general") == ()
    reader = skill_reader(manager.for_agent("product_technical"))
    assert "MAIN_ONLY" not in reader.description
    assert "REFERENCE_ONLY" not in reader.description
    args = {"name": skill.name, "version": skill.version}
    assert json.loads(asyncio.run(reader.ainvoke(args)))["instructions"] == "MAIN_ONLY"
    assert json.loads(asyncio.run(reader.ainvoke({**args, "resource": "references/detail.md"})))["instructions"] == "REFERENCE_ONLY"


@pytest.mark.parametrize("resource", ["../secret", "/etc/passwd", "references/../../secret", "scripts/run.py"])
def test_reader_accepts_only_snapshot_resources(tmp_path, resource):
    package(tmp_path)
    manager = SkillManager(str(tmp_path))
    skill, = manager.load()
    output = asyncio.run(skill_reader((skill,)).ainvoke(
        {"name": skill.name, "version": skill.version, "resource": resource}))
    assert output.startswith("SKILL_RESOURCE_NOT_FOUND")


def test_reload_changes_version_but_not_inflight_snapshot(tmp_path):
    directory = package(tmp_path)
    manager = SkillManager(str(tmp_path))
    old, = manager.load()
    reader = skill_reader((old,))
    (directory / "references" / "detail.md").write_text("REVISED")
    new, = manager.reload()
    assert old.version != new.version
    args = {"name": old.name, "version": old.version, "resource": "references/detail.md"}
    assert json.loads(asyncio.run(reader.ainvoke(args)))["instructions"] == "REFERENCE_ONLY"
    assert asyncio.run(skill_reader((new,)).ainvoke(args)).startswith("SKILL_VERSION_CHANGED")
    assert asyncio.run(skill_reader(()).ainvoke(args)).startswith("SKILL_NOT_AVAILABLE")
    (directory / "SKILL.md").write_text("invalid")
    with pytest.raises(ValueError):
        manager.reload()
    assert manager.skills == (new,)


@pytest.mark.parametrize("escape", ["main", "reference", "package"])
def test_loader_rejects_external_symlink(tmp_path, escape):
    root = tmp_path / "skills"
    directory = package(root)
    outside = tmp_path / "outside.md"
    outside.write_text("private")
    if escape == "package":
        (root / "linked").symlink_to(tmp_path, target_is_directory=True)
        (tmp_path / "SKILL.md").write_text("private")
    else:
        target = directory / ("SKILL.md" if escape == "main" else "references/detail.md")
        target.unlink()
        target.symlink_to(outside)
    with pytest.raises(ValueError, match="OUTSIDE_PACKAGE"):
        SkillManager(str(root)).load()


def test_repository_package_has_single_loader_and_explicit_domains():
    manager = SkillManager(str(Path(__file__).resolve().parents[1] / "skills"))
    skill, = manager.load()
    assert skill.name == "product_upgrade"
    assert set(skill.agents) == {"product_technical", "retail"}
    assert set(skill.resources) == {"SKILL.md", "references/comparison.md", "examples/cases.md"}


def test_invalid_yaml_reload_preserves_catalog(tmp_path):
    directory = package(tmp_path)
    manager = SkillManager(str(tmp_path))
    previous = manager.load()
    (directory / "SKILL.md").write_text("---\nname: [\n---\nbody")
    with pytest.raises(ValueError, match="INVALID_YAML"):
        manager.reload()
    assert manager.skills == previous


def test_duplicate_names_rejected_and_resources_immutable(tmp_path):
    import shutil
    directory = package(tmp_path)
    manager = SkillManager(str(tmp_path))
    previous = manager.load()
    with pytest.raises(TypeError):
        previous[0].resources["SKILL.md"] = "overwrite"
    shutil.copytree(directory, tmp_path / "duplicate")
    with pytest.raises(ValueError, match="DUPLICATE_SKILL_NAME"):
        manager.reload()
    assert manager.skills == previous


def test_framework_reads_guidance_without_turning_it_into_business_evidence(tmp_path):
    from langchain_core.messages import AIMessage
    from langgraph.store.memory import InMemoryStore
    from application.default_capability_registry import build_default_capability_registry
    from infrastructure.target_framework_agent import TargetFrameworkAgent
    from tests.test_target_framework_agent import ScriptedToolModel, _context, _manager

    package(tmp_path)
    catalog = SkillManager(str(tmp_path))
    skill, = catalog.load()
    model = ScriptedToolModel(responses=[
        AIMessage(content="", tool_calls=[{"id": "guide", "name": "read_skill", "args": {
            "name": skill.name, "version": skill.version}}]),
        AIMessage(content="", tool_calls=[{"id": "catalog", "name": "catalog_search", "args": {"query": "product"}}]),
        AIMessage(content="The model is PX-200."),
    ])
    calls = []
    agent = TargetFrameworkAgent(model, _manager(calls), review_model=model,
        review_available_tokens=14200, result_store=InMemoryStore(),
        registry=build_default_capability_registry("tenant-a"),
        system_prompt="Resolve product questions.", skill_manager=catalog)
    result = asyncio.run(agent(_context()))
    assert len(calls) == 1
    assert len(result.facts) == 1
    assert result.facts[0].source_ref == "catalog"
    assert "read_skill" in model.bound_tool_names
    assert not any(name.startswith("prepare_") for name in model.bound_tool_names)
    assert any("MAIN_ONLY" in json.dumps(message) for message in result.working_messages)
