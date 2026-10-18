"""Home Assistant workspace: a real, public codebase (home-assistant/core) and its external consumers (HACS).

The importer (importer.py) turns a core checkout plus a folder of custom integrations into a snapshot file.
load.py puts the snapshot into FalkorDB. impact.py answers "who breaks if this change merges".
"""
