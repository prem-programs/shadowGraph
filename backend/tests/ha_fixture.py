"""A miniature Home Assistant: a git repo shaped like home-assistant/core (two release tags and a dev branch
with one squash-merged PR) plus a folder of HACS-style custom integrations. Built fresh for each test session.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@example.com", "GIT_AUTHOR_DATE": "2026-10-01T10:00:00Z",
       "GIT_COMMITTER_DATE": "2026-10-01T10:00:00Z"}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, env=ENV).stdout


def write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


CORE_V1 = {
    "homeassistant/__init__.py": "",
    "homeassistant/generated/__init__.py": "",
    "homeassistant/generated/entity_platforms.py": (
        "from enum import StrEnum\n\n\nclass EntityPlatforms(StrEnum):\n    SENSOR = \"sensor\"\n    LIGHT = \"light\"\n"),
    "homeassistant/const.py": '''"""Constants."""
from enum import StrEnum
from functools import partial

from .generated.entity_platforms import EntityPlatforms
from .helpers.deprecation import DeprecatedConstant, DeprecatedConstantEnum, check_if_deprecated_constant

Platform = EntityPlatforms


class EntityCategory(StrEnum):
    CONFIG = "config"
    DIAGNOSTIC = "diagnostic"


class UnitOfRatio(StrEnum):
    PARTS_PER_MILLION = "ppm"


TEMP_OLD = "°C"
_DEPRECATED_CONN_CLASS_CLOUD_POLL = DeprecatedConstant("cloud_poll", None, "2027.5.0")
_DEPRECATED_CONCENTRATION_PARTS_PER_MILLION = DeprecatedConstantEnum(UnitOfRatio.PARTS_PER_MILLION, "2027.8")
_PRIVATE_THING = 1

try:
    pass
except KeyError, ValueError:
    pass

__getattr__ = partial(check_if_deprecated_constant, module_globals=globals())
''',
    "homeassistant/core.py": "class HomeAssistant:\n    pass\n\n\ndef callback(func):\n    return func\n",
    "homeassistant/helpers/__init__.py": "",
    "homeassistant/helpers/deprecation.py": '''from typing import NamedTuple


class DeprecatedConstant(NamedTuple):
    value: object
    replacement: str | None
    breaks_in_ha_version: str | None


class DeprecatedConstantEnum(NamedTuple):
    enum: object
    breaks_in_ha_version: str | None


def check_if_deprecated_constant(name, module_globals):
    return None


def deprecated_function(replacement, breaks_in_ha_version=None):
    def wrap(f):
        return f
    return wrap
''',
    "homeassistant/helpers/typing.py": "from typing import Any\n\ntype StateType = str | int | float | None\ntype ConfigType = dict[str, Any]\n",
    "homeassistant/helpers/event.py": '''from .deprecation import deprecated_function


@deprecated_function("async_track_state_change_event", breaks_in_ha_version="2026.12.0")
def async_track_state_change(hass, entity_ids, action):
    return None


def async_track_state_change_event(hass, entity_ids, action):
    return None
''',
    "homeassistant/components/__init__.py": "",
    "homeassistant/components/sensor/__init__.py": '''"""Sensor entity platform."""
from enum import StrEnum

from homeassistant.const import EntityCategory  # used below; re-exported by accident
from homeassistant.helpers.typing import StateType


class SensorDeviceClass(StrEnum):
    TEMPERATURE = "temperature"


class SensorEntity:
    entity_category: EntityCategory | None = None
    native_value: StateType = None
''',
    "homeassistant/components/sensor/manifest.json": json.dumps(
        {"domain": "sensor", "name": "Sensor", "codeowners": ["@home-assistant/core"], "integration_type": "entity"}),
    "homeassistant/components/demo/__init__.py": "from homeassistant.core import HomeAssistant\n\n\nasync def async_setup(hass: HomeAssistant, config):\n    return True\n",
    "homeassistant/components/demo/sensor.py": (
        "from homeassistant.components.sensor import SensorDeviceClass, SensorEntity\n\n\n"
        "class DemoSensor(SensorEntity):\n    device_class = SensorDeviceClass.TEMPERATURE\n"),
    "homeassistant/components/demo/manifest.json": json.dumps(
        {"domain": "demo", "name": "Demo", "codeowners": ["@demo-owner"], "dependencies": ["sensor"]}),
    "CODEOWNERS": "/homeassistant/helpers/ @home-assistant/core\n/homeassistant/components/demo/ @demo-owner\n",
}

# 2026.10.0: TEMP_OLD is removed (history: removed in 2026.10)
CORE_V2_CHANGES = {"homeassistant/const.py": CORE_V1["homeassistant/const.py"].replace('TEMP_OLD = "°C"\n', "")}

# PR #101 on dev: the sensor component stops importing EntityCategory (so the accidental re-export disappears)
PR_SENSOR = CORE_V1["homeassistant/components/sensor/__init__.py"].replace(
    "from homeassistant.const import EntityCategory  # used below; re-exported by accident\n", "").replace(
    "    entity_category: EntityCategory | None = None\n", "    entity_category: str | None = None\n")

HACS = {
    "alice_breaks": {
        "hacs.json": json.dumps({"name": "Breaks", "homeassistant": "2026.9.0"}),
        "custom_components/breaks/manifest.json": json.dumps({"domain": "breaks", "name": "Breaks Integration",
                                                              "codeowners": ["@alice"], "version": "1.0"}),
        "custom_components/breaks/__init__.py": (
            "from homeassistant.components.sensor import EntityCategory, SensorEntity\n"
            "from .const import DOMAIN\n\n\nasync def async_setup_entry(hass, entry):\n    return True\n"),
        "custom_components/breaks/const.py": "DOMAIN = 'breaks'\n",
        "custom_components/breaks/config_flow.py": (
            "from homeassistant.const import CONN_CLASS_CLOUD_POLL\n\n\n"
            "class Flow:\n    VERSION = 1\n    CONNECTION_CLASS = CONN_CLASS_CLOUD_POLL\n"),
    },
    "bob_shim": {
        "hacs.json": json.dumps({"name": "Shim"}),
        "custom_components/shim/manifest.json": json.dumps({"domain": "shim", "name": "Shim Integration"}),
        "custom_components/shim/sensor.py": (
            "try:\n    from homeassistant.components.sensor import EntityCategory\n"
            "except ImportError:\n    from homeassistant.const import EntityCategory\n"),
    },
    "carol_typeonly": {
        "hacs.json": json.dumps({"name": "Typeonly"}),
        "custom_components/typeonly/manifest.json": json.dumps({"domain": "typeonly", "name": "Type Only"}),
        "custom_components/typeonly/sensor.py": (
            "from homeassistant.components import sensor\n\n\n"
            "def category(x: sensor.EntityCategory) -> None:\n"
            "    entities = [sensor for sensor in range(3)]\n"
            "    return [sensor.real for sensor in entities]\n"),
    },
    "dave_child": {
        "hacs.json": json.dumps({"name": "Child"}),
        "custom_components/child/manifest.json": json.dumps({"domain": "child", "name": "Child Integration",
                                                             "dependencies": ["breaks"]}),
        "custom_components/child/__init__.py": "from homeassistant.core import HomeAssistant\n",
        "custom_components/child/light.py": (
            "from homeassistant.const import CONCENTRATION_PARTS_PER_MILLION\n"
            "from homeassistant.helpers import event\n\n\n"
            "def setup(hass):\n    event.async_track_state_change(hass, [], None)\n"
            "    return CONCENTRATION_PARTS_PER_MILLION\n"),
    },
}


def build(tmp: Path) -> dict[str, str]:
    core = tmp / "core"
    core.mkdir()
    git(core, "init", "-q", "-b", "dev")
    write(core, CORE_V1)
    git(core, "add", "-A")
    git(core, "commit", "-q", "-m", "Initial (#1)")
    git(core, "tag", "2026.9.0")
    write(core, CORE_V2_CHANGES)
    git(core, "commit", "-qam", "Remove TEMP_OLD (#50)")
    git(core, "tag", "2026.10.0")
    write(core, {"homeassistant/components/sensor/__init__.py": PR_SENSOR})
    git(core, "commit", "-qam", "Stop importing EntityCategory in sensor (#101)")
    sha = git(core, "rev-parse", "HEAD").strip()
    git(core, "update-ref", "refs/remotes/origin/dev", "HEAD")
    hacs = tmp / "hacs"
    hacs.mkdir()
    for name, files in HACS.items():
        repo = hacs / name
        repo.mkdir()
        git(repo, "init", "-q")
        write(repo, files)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "init")
    return {"core": str(core), "hacs": str(hacs), "pr_sha": sha, "base_ref": "2026.10.0"}
