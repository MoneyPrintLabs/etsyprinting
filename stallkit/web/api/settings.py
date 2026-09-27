"""Ayarlar: the products folder and the setup checklist.

    GET  /api/settings             version, licence, repository, the folders
    POST /api/settings/workspace   {"path": "<absolute folder>" | ""} ("" = the default)
    POST /api/settings/doctor      run the setup checklist (`stallkit doctor`) now

A folder of a workspace (1-MOCKUPS, 2-PRODUCTS, 3-DRAFTS, anything inside them) chosen
as the workspace means that workspace: its root is saved, never a second workspace
nested inside the first (`drop.workspace.root_for`). The answer then carries
`adjusted: {"chosen", "root"}` so the page can say so. A folder saved that way by an
older version is reported as `workspace.nested_in` (the root it belongs to).

Language, hiding shop names, the shop list and quitting use the core endpoints
(/api/prefs, /api/shops/*, /api/quit); the Etsy connection uses /api/connect/*.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...desktop import settings as desktop_settings
from ...errors import EtsyApiError, StallKitError
from ..router import ApiError, Request

if TYPE_CHECKING:  # pragma: no cover
    from ..context import AppContext
    from ..router import Router

LICENSE = "MIT"
REPO_URL = "https://github.com/MoneyPrintLabs/etsyprinting"
# setup.build_steps numbers its steps; the page words each one by this key.
STEP_KEYS = {
    1: "python", 2: "installed", 3: "shop", 4: "app", 5: "credentials", 6: "redirect",
    7: "callback", 8: "api", 9: "connected", 10: "workspace",
}


def register(r: Router, ctx: AppContext) -> None:
    r.get("/api/settings", overview)
    r.post("/api/settings/workspace", set_workspace)
    r.post("/api/settings/doctor", doctor)


def _ctx(req: Request) -> AppContext:
    assert req.ctx is not None
    return req.ctx


def _same_path(a: Path, b: Path) -> bool:
    """The same folder as far as the path says (case and separators as the OS sees them)."""
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


def _folders(ctx: AppContext) -> dict[str, Any]:
    from ...drop import workspace as workspace_mod

    root = ctx.workspace_root()
    ws = workspace_mod.Workspace(root)
    paths = {"workspace": ws.root, "mockups": ws.mockups, "products": ws.products,
             "drafts": ws.drafts}
    belongs_to = workspace_mod.root_for(root) if root.is_absolute() else root
    return {
        "root": str(root),
        "default": str(workspace_mod.default_root()),
        "custom": bool(str(ctx.shop_prefs().get("workspace") or "").strip()),
        # Saved before the guard: a folder of another workspace (see the module doc).
        "nested_in": None if _same_path(belongs_to, root) else str(belongs_to),
        "folders": [
            {"which": which, "name": path.name, "path": str(path), "exists": path.is_dir()}
            for which, path in paths.items()
        ],
    }


def overview(req: Request) -> dict[str, Any]:
    ctx = _ctx(req)
    return {
        "version": ctx.version,
        "license": LICENSE,
        "repo": REPO_URL,
        "env_file": str(desktop_settings.env_path()),
        "workspace": _folders(ctx),
    }


def set_workspace(req: Request) -> dict[str, Any]:
    """Use another products folder for the open shop (created, with its subfolders).

    A folder of a workspace means that workspace (module doc); the default folder,
    however it is reached, is saved as "the default" rather than as a custom path.
    """
    from ...drop import workspace as workspace_mod

    ctx = _ctx(req)
    body = req.json_object()
    raw = body.get("path")
    if not isinstance(raw, str):
        raise ApiError(422, "invalid", "path must be text", field="path")
    raw = raw.strip().strip('"').strip("'").strip()
    if ctx.jobs.busy():
        raise ApiError(409, "busy", "Wait for the running task to finish first.")
    adjusted: dict[str, str] | None = None
    if not raw:
        ctx.update_shop_prefs(workspace=None)
    else:
        chosen = Path(raw).expanduser()
        if not chosen.is_absolute():
            raise ApiError(422, "workspace_not_absolute", "Give the full path of a folder.",
                           field="path")
        if chosen.exists() and not chosen.is_dir():
            raise ApiError(422, "workspace_not_folder", "That is a file, not a folder.",
                           field="path")
        path = workspace_mod.root_for(chosen)
        if not _same_path(path, chosen):
            adjusted = {"chosen": str(chosen), "root": str(path)}
        try:
            workspace_mod.Workspace(path).create()
        except OSError as exc:
            raise ApiError(422, "workspace_unwritable",
                           f"Cannot create the folders there: {exc.strerror or exc}",
                           field="path") from exc
        default = _same_path(path, workspace_mod.default_root())
        ctx.update_shop_prefs(workspace=None if default else str(path))
    try:
        ctx.workspace()
    except OSError as exc:
        raise ApiError(422, "workspace_unwritable",
                       f"Cannot create the folders there: {exc.strerror or exc}",
                       field="path") from exc
    ctx.set_status_soon(0.0)
    return {"workspace": _folders(ctx), "adjusted": adjusted}


def _check_api(ctx: AppContext) -> Any:
    """Step 8 through the app's own client (its rate limit, one quick try)."""
    from ... import setup

    try:
        client = ctx.client(require_auth=False)
    except ApiError:
        return setup.StepResult(setup.MISSING, "no credentials to test with",
                                ["Finish step 5 first."])
    try:
        with client.attempts(1):
            client.ping()
    except EtsyApiError as exc:
        if exc.status == 0:
            return setup.StepResult(setup.WARN, f"Etsy cannot be reached right now: {exc.message}")
        return setup.StepResult(setup.MISSING, str(exc).splitlines()[0],
                                ["Check BOTH halves on your app page at "
                                 "https://www.etsy.com/developers/your-apps"])
    return setup.StepResult(setup.OK, "Etsy accepted your keystring and shared secret")


def doctor(req: Request) -> dict[str, Any]:
    """setup.build_steps, checked now. Steps only the person can answer stay "unknown"."""
    from ... import setup

    ctx = _ctx(req)
    results = []
    items = []
    for step in setup.build_steps(ctx.workspace_root()):
        if step.check is setup.check_api_reachable:
            result = _check_api(ctx)
        else:
            try:
                result = step.check()
            except (StallKitError, OSError) as exc:
                result = setup.StepResult(setup.MISSING, str(exc).splitlines()[0] if str(exc) else "")
        results.append((step, result))
        items.append({
            "number": step.number,
            "key": STEP_KEYS.get(step.number, str(step.number)),
            "title": step.title,
            "state": result.state,
            "detail": result.detail,
            "fix": list(result.fix),
            "required": step.required,
            "question": step.question,
        })
    return {
        "items": items,
        "ok": sum(1 for item in items if item["state"] == setup.OK),
        "total": len(items),
        "next": setup.next_command(results),
        "checked_at": round(time.time(), 3),
    }
