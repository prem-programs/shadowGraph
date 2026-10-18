"""Home Assistant workspace: static analysis, the snapshot, impact reports, drafts, API, MCP and agents.

Runs on the miniature Home Assistant from tests/ha_fixture.py (a real git repo and real HACS-style folders).
"""
from __future__ import annotations

import httpx
from mcp import Client

from shadowgraph.api import app
from shadowgraph.db import PREFIX
from shadowgraph.ha import migrate, service
from shadowgraph.ha.agents import run_change
from shadowgraph.ha.pyparse import parse
from shadowgraph.ha.surface import EXISTS, MISSING, UNKNOWN, Resolver, module_surface, signature_change
from shadowgraph.ha.usage import scan_source
from shadowgraph.mcp_server import server

from .ha_fixture import CORE_V1

SENSOR_EC = "homeassistant.components.sensor.EntityCategory"


# ------------------------------------------------------------------------------------------- static analysis

def test_parses_python_314_except_syntax():
    assert parse("try:\n    pass\nexcept KeyError, ValueError:\n    pass\n") is not None
    assert parse("def broken(:\n") is None


def _resolver(files: dict[str, str]) -> Resolver:
    from shadowgraph.ha.surface import module_name

    mods = {module_name(p): p for p in files if p.endswith(".py")}
    cache = {}

    def get(mod):
        if mod not in cache:
            cache[mod] = module_surface(files[mods[mod]], mod) if mod in mods else None
        return cache[mod]

    return Resolver(get, lambda m: mods.get(m, "").endswith("__init__.py"))


def test_surface_and_resolver():
    r = _resolver(CORE_V1)
    const = module_surface(CORE_V1["homeassistant/const.py"], "homeassistant.const")
    dep = const.exports["CONN_CLASS_CLOUD_POLL"].deprecated
    assert dep == {"how": "DeprecatedConstant", "replacement": None, "breaks_in": "2027.5.0"}
    enum_dep = const.exports["CONCENTRATION_PARTS_PER_MILLION"].deprecated
    assert enum_dep["replacement"] == "UnitOfRatio.PARTS_PER_MILLION" and enum_dep["breaks_in"] == "2027.8"
    assert const.exports["_PRIVATE_THING"].private
    assert r.state("homeassistant.components.sensor", "EntityCategory") == EXISTS          # accidental re-export
    assert r.state("homeassistant.const", "Platform.SENSOR") == EXISTS                     # alias -> enum member
    assert r.state("homeassistant.components.sensor", "SensorDeviceClass.TEMPERATURE") == EXISTS
    assert r.state("homeassistant.components.sensor", "SensorDeviceClass.NOPE") == MISSING
    assert r.state("homeassistant.helpers.typing", "StateType") == EXISTS                  # PEP 695 alias
    assert r.state("homeassistant.const", "NOT_THERE") == MISSING
    assert r.state("enum", "StrEnum") == UNKNOWN                                           # third party: never "missing"
    assert r.state("homeassistant.components", "sensor") == EXISTS                          # submodule as attribute
    assert signature_change([["a", "pos", False]], [["a", "pos", False], ["b", "pos", False]]) == ["new required parameter `b`"]


def test_usage_contexts_and_shadowing():
    known = {"homeassistant", "homeassistant.components", "homeassistant.components.sensor", "homeassistant.const",
             "homeassistant.helpers", "homeassistant.helpers.event"}
    src = '''
from typing import TYPE_CHECKING
from homeassistant.components.sensor import SensorEntity
from homeassistant.components import sensor
from homeassistant.helpers import event
try:
    from homeassistant.const import NEW_THING
except ImportError:
    NEW_THING = None
if TYPE_CHECKING:
    from homeassistant.const import OnlyTypes
if VERSION > 1:
    from homeassistant.const import MAYBE
def f(x: sensor.EntityCategory):
    event.async_track_state_change(1, 2, 3)
    event = 5
    return [sensor.x for sensor in range(3)]
class Flow:
    KIND = sensor.SensorDeviceClass.TEMPERATURE
'''
    scan = scan_source(src, known, [])
    ctx = {(u.module.rsplit(".", 1)[-1], u.name): u.context for u in scan.uses}
    assert ctx[("sensor", "SensorEntity")] == "load"
    assert ctx[("const", "NEW_THING")] == "shimmed"
    assert ctx[("const", "OnlyTypes")] == "type"
    assert ctx[("const", "MAYBE")] == "conditional"
    assert ctx[("sensor", "EntityCategory")] == "type"
    assert ctx[("sensor", "SensorDeviceClass.TEMPERATURE")] == "load"
    # `event` is rebound inside f, and `sensor` is a comprehension variable: neither is the module there
    assert ("event", "async_track_state_change") not in ctx
    assert ("sensor", "x") not in ctx


# ------------------------------------------------------------------------------------------- snapshot

def test_snapshot_self_check(ha_fixture):
    snap = ha_fixture["snap"]
    assert snap["meta"]["core_integrations"] == 2 and snap["meta"]["external_integrations"] == 4
    assert snap["meta"]["parse_failures"] == {"core": 0, "external": 0}
    syms = snap["symbols"]
    # every core integration's import resolves in core itself (no false "missing")
    core_ids = {i for i, it in enumerate(snap["integrations"]) if it["kind"] == "core"}
    assert not [u for u in snap["uses"] if u[0] in core_ids and syms[u[1]]["state"] == MISSING]
    assert syms[SENSOR_EC]["state"] == MISSING and syms[SENSOR_EC]["removed_in"] == "2026.11"
    assert syms["homeassistant.const.CONN_CLASS_CLOUD_POLL"]["deprecated"]["release_date"] == "2027-05-05"
    entries = next(i for i in snap["integrations"] if i["id"] == "hacs:alice/breaks")["files"]
    assert entries["custom_components/breaks/__init__.py"] == ["setup"]
    assert entries["custom_components/breaks/config_flow.py"] == ["config_flow"]


# ------------------------------------------------------------------------------------------- impact

async def test_replay_finds_the_breakage(rt, ha_fixture):
    r = await service.analyze_commit(rt, ha_fixture["pr_sha"], save=False)
    assert [s["id"] for s in r["api"]["removed"] if not s.get("consequence_of")] == [SENSOR_EC]
    by = {c["id"]: c for c in r["consumers"]}
    assert by["hacs:alice/breaks"]["level"] == "breaks"
    assert by["hacs:alice/breaks"]["effect"] == "the integration fails to set up"
    assert by["hacs:bob/shim"]["level"] == "safe"            # try/except fallback
    assert by["hacs:carol/typeonly"]["level"] == "safe"      # annotation only
    assert [c["id"] for c in r["cascade"]] == ["hacs:dave/child"]   # depends on the integration that fails
    assert r["verdict"]["level"] == "breaks" and r["verdict"]["counts"]["external_breaks"] == 1
    assert r["next_release"]["version"] == "2026.11"


async def test_preflight_of_a_planned_edit(rt):
    src = CORE_V1["homeassistant/components/sensor/__init__.py"].replace("class SensorDeviceClass", "class OtherName")
    r = await service.analyze_edits(rt, {"homeassistant/components/sensor/__init__.py": src}, "Rename SensorDeviceClass")
    assert r["verdict"]["level"] == "breaks" and r["verdict"]["counts"]["core_breaks"] == 1
    demo = next(c for c in r["consumers"] if c["id"] == "core:demo")
    assert demo["effect"] == "its sensor platform fails to load" and demo["fixed_in_pr"] is False


async def test_retirements_and_broken(rt):
    board = await service.retirements(rt)
    conn = next(c for c in board["cards"] if c["family"] == "CONN_CLASS_CLOUD_POLL")
    assert conn["breaks_in"] == "2027.5.0" and conn["blocking"] == 1 and conn["status"] == "migrating"
    broken = await service.already_broken(rt)
    assert {i["integration"]["id"] for i in broken["items"]} == {"hacs:alice/breaks"}
    assert broken["items"][0]["released"] is False   # breaks in the next release, not yet released


def test_migration_drafts():
    known = {"homeassistant", "homeassistant.const", "homeassistant.components", "homeassistant.components.sensor"}
    src = "from homeassistant.components.sensor import EntityCategory, SensorEntity\nfrom .const import DOMAIN\n"
    plan = migrate.plan_for({"module": "homeassistant.components.sensor", "name": "EntityCategory"},
                            [{"module": "homeassistant.const"}], known)
    new, _ = migrate.apply_plan(src, plan, known)
    assert new == ("from homeassistant.components.sensor import SensorEntity\n"
                   "from homeassistant.const import EntityCategory\nfrom .const import DOMAIN\n")
    assert migrate.validate(new, SENSOR_EC, known) is None

    drop = migrate.plan_for({"module": "homeassistant.const", "name": "CONN_CLASS_CLOUD_POLL", "deprecated": True,
                             "replacement": ""}, [], known)
    flow = "from homeassistant.const import CONN_CLASS_CLOUD_POLL\n\n\nclass Flow:\n    VERSION = 1\n    CONNECTION_CLASS = CONN_CLASS_CLOUD_POLL\n"
    new, notes = migrate.apply_plan(flow, drop, known)
    assert "CONN_CLASS" not in new and "VERSION = 1" in new and notes

    repl = migrate.plan_for({"module": "homeassistant.const", "name": "CONCENTRATION_PARTS_PER_MILLION", "deprecated": True,
                             "replacement": "UnitOfRatio.PARTS_PER_MILLION"}, [], known)
    code = "from homeassistant.const import CONCENTRATION_PARTS_PER_MILLION\n\nUNIT = CONCENTRATION_PARTS_PER_MILLION\n"
    new, _ = migrate.apply_plan(code, repl, known)
    assert "UNIT = UnitOfRatio.PARTS_PER_MILLION" in new and "from homeassistant.const import UnitOfRatio" in new
    assert migrate.validate(new, "homeassistant.const.CONCENTRATION_PARTS_PER_MILLION", known) is None


# ------------------------------------------------------------------------------------------- API, MCP, agents

async def test_ha_api(rt):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        o = (await c.get("/api/ha/overview")).json()
        assert o["loaded"] and o["external_integrations"] == 4 and o["breaks_next"] == 1
        d = (await c.get(f"/api/ha/symbols/{SENSOR_EC}")).json()
        assert d["summary"]["would_break_external"] == 1 and d["alternatives"][0]["module"] == "homeassistant.const"
        hits = (await c.get("/api/ha/symbols", params={"q": "EntityCat"})).json()
        assert any(h["id"] == "homeassistant.const.EntityCategory" for h in hits)
        assert (await c.get("/api/ha/changes/PR-999")).status_code == 404
        r = await c.post("/api/ha/preflight", json={"files": [{"path": "homeassistant/helpers/event.py", "content": ""}]})
        assert r.status_code == 200 and r.json()["verdict"]["level"] == "breaks"


def _data(res) -> dict:
    import json

    assert not res.is_error, "".join(getattr(c, "text", "") for c in res.content or [])
    if res.structured_content is not None:
        d = res.structured_content
        return d.get("result", d) if set(d) == {"result"} else d
    return json.loads("".join(getattr(c, "text", "") for c in res.content or []))


async def test_ha_mcp_tools(rt):
    async with Client(server, raise_exceptions=True) as client:
        data = _data(await client.call_tool("ha_can_remove",
                                            {"symbol": "homeassistant.helpers.event.async_track_state_change"}))
        assert data["can_remove"] is False and data["would_break"][0]["integration"] == "Child Integration"
        data = _data(await client.call_tool("ha_who_uses", {"symbol": SENSOR_EC}))
        assert data["users_total"] == 3 and data["summary"]["would_break"] == 1


async def test_ha_agent_run(rt, ha_fixture):
    out = await run_change(rt, 101, ha_fixture["pr_sha"], "rtest_ha", workers=3)
    report = out["report"]
    assert report["verdict"]["level"] == "breaks"
    assert report["cross_check"] == {"engine_external_breaks": 1, "shadow_external_breaks": 1, "agree": True}
    status = {h["check"]: h["status"] for h in report["hypotheses"]}
    assert status["consumers"] == "CONFIRMED" and status["cascade"] == "CONFIRMED"
    assert report["checks"]["cascade"]["measure"]["dependents_failing"] == 1
    assert not [g for g in await rt.db.list_graphs() if g.startswith(f"{PREFIX}ha_sim_")]   # shadows dropped
    stored = await service.get_change(rt, "PR-101")
    assert stored and stored["verdict"]["headline"].startswith("Breaks 1 custom integration on 2026.11")


async def test_checks_survive_a_snapshot_reload(rt, ha_fixture):
    from shadowgraph.ha.load import load_snapshot

    assert await service.get_change(rt, "PR-101") is not None
    await load_snapshot(rt, ha_fixture["snap"])
    stored = await service.get_change(rt, "PR-101")
    assert stored and stored["verdict"]["level"] == "breaks"
    rows = await rt.db.query(f"{PREFIX}ha_live", "MATCH (:Change {id: 'PR-101'})-[r:CHANGES]->(s) RETURN s.id AS id")
    assert [r["id"] for r in rows] == [SENSOR_EC]


async def test_github_source_reads_a_pr_without_writing():
    """The REST source against a fake GitHub: only GET requests, merge base from the compare endpoint."""
    from shadowgraph.ha.sources import GitHubSource

    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        p = request.url.path
        if p == "/repos/home-assistant/core/pulls/7":
            return httpx.Response(200, json={"number": 7, "title": "Real title", "user": {"login": "me"},
                                             "head": {"sha": "h" * 40}, "base": {"sha": "b" * 40},
                                             "html_url": "https://github.com/home-assistant/core/pull/7",
                                             "state": "open", "merged_at": None, "labels": [{"name": "core"}]})
        if p.startswith("/repos/home-assistant/core/compare/"):
            return httpx.Response(200, json={"merge_base_commit": {"sha": "m" * 40}})
        if p == "/repos/home-assistant/core/pulls/7/files":
            return httpx.Response(200, json=[{"filename": "homeassistant/const.py", "status": "modified"},
                                             {"filename": "homeassistant/new.py", "status": "added"}])
        if request.url.host == "raw.githubusercontent.com":
            return httpx.Response(200, text="X = 1\n")
        return httpx.Response(404)

    src = GitHubSource(core_files=["homeassistant/const.py"])
    src.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    change, base, head = await src.fetch_pr(7)
    await src.close()
    assert change.title == "Real title" and change.base_sha == "m" * 40 and not change.title_is_commit
    assert [f.status for f in change.files] == ["modified", "added"]
    assert "homeassistant.new" in head.modules() and "homeassistant.new" not in base.modules()
    assert base.read("homeassistant/const.py") == "X = 1\n"
    assert all(method == "GET" for method, _ in seen)
