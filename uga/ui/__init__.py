from __future__ import annotations

from importlib import resources

from uga.core.errors import ContractViolation


def load_ui_script(name: str) -> str:
    if not name.endswith(".js") or "/" in name or "\\" in name:
        raise ContractViolation("UI asset name must be a JavaScript file name")
    asset = resources.files("uga.ui").joinpath("static", name)
    if not asset.is_file():
        raise ContractViolation(f"compiled UI asset is missing: {name}")
    return asset.read_text(encoding="utf-8")
