#!/usr/bin/env python3
"""Cython extension build entrypoint for DRL-OR-S delivery packages."""

from __future__ import annotations

import json
import os
from pathlib import Path

from setuptools import Extension, setup


def _load_delivery_extensions():
    manifest_path = os.environ.get("DRL_ORS_CYTHON_MANIFEST")
    if not manifest_path:
        return []
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    extensions = []
    for item in manifest.get("sources", []):
        extensions.append(
            Extension(
                item["module"],
                [item["build_source"]],
            )
        )
    return extensions


extensions = _load_delivery_extensions()

if extensions:
    from Cython.Build import cythonize

    ext_modules = cythonize(
        extensions,
        compiler_directives={
            "language_level": "3",
            "binding": False,
            "embedsignature": False,
        },
    )
else:
    ext_modules = []


setup(
    name="drl-ors-routing-suite",
    version="0.1.0",
    ext_modules=ext_modules,
)
