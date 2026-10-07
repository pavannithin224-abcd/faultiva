# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Faultiva desktop app.

Data files are collected explicitly rather than with a glob, so a missing
catalogue fails the build instead of producing an app that starts and then
cannot diagnose anything.

sklearn and uvicorn both load parts of themselves dynamically, so their
submodules are named in hiddenimports; without that the frozen app imports
cleanly and then fails at the first request.
"""
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

# payload root: the repo this spec lives in, or FAULTIVA_STAGE when the
# build runs from a staging copy
import os
STAGE = Path(os.environ.get("FAULTIVA_STAGE",
                            Path(SPECPATH).resolve().parent))

REQUIRED = [
    "models/faultiva_signature_dictionary.npz",
    "models/hmac_hybrid_v1_original.joblib",
    "models/hmac_final_diagnostic_model_lock_11d2d.json",
    "models/hmac_hybrid_architecture_11d2a.json",
    "models/hmac_leakage_safe_feature_matrix_schema_11c5e.json",
    "data/faultiva_golden_baselines.npz",
    "data/faultiva_example_captures.npz",
    "data/faultiva_cell_types.npz",
    "data/faultiva_site_names.npz",
    "data/hmac_directed_sgc_graph_features_11d1c.npz",
    "data/hmac_golden_netlist_graph_11d1a.npz",
    "docs/FEATURE_SCALING_CONVENTION.json",
    "app/ui/index.html",
]

datas = []
for rel in REQUIRED:
    src = STAGE / rel
    if not src.is_file():
        raise SystemExit(f"spec: required payload missing: {rel}")
    datas.append((str(src), str(Path(rel).parent)))

# documentation and the worked example, shipped so the app can show them
for rel in ("docs/CHARACTERIZATION.md", "docs/MODEL_CARD.md",
            "docs/HONEST_LIMITS.md", "README.md", "LICENSE", "CITATION.cff"):
    src = STAGE / rel
    if src.is_file():
        datas.append((str(src), str(Path(rel).parent)))

for sub in (STAGE / "examples").rglob("*"):
    if sub.is_file():
        datas.append((str(sub), str(sub.parent.relative_to(STAGE))))

hiddenimports = [
    "faultiva", "faultiva.pipeline", "faultiva.signatures",
    "faultiva.feature_pipeline", "faultiva.toolchain",
    "faultiva.characterize", "faultiva.characterize.config",
    "faultiva.characterize.netlist", "faultiva.characterize.testbench",
    "faultiva.characterize.stimulus", "faultiva.characterize.campaign",
    "faultiva.characterize.selftest",
    "app",
    "sklearn.neural_network._multilayer_perceptron",
    "sklearn.preprocessing._label",
    "sklearn.utils._typedefs",
    "joblib",
    "multipart",
]
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("starlette")

a = Analysis(
    [str(STAGE / "desktop" / "faultiva_app.py")],
    pathex=[str(STAGE)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PIL", "pandas", "scipy.spatial.cKDTree",
              "IPython", "pytest", "setuptools._distutils"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Faultiva",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False,
    name="Faultiva",
)
