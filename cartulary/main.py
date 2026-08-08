"""FastAPI surface over the symbol graph."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from .corpus import CORPUS
from .graph import ResolutionError, build, impact

app = FastAPI(title="Cartulary", docs_url="/api/docs")
WEB = Path(__file__).resolve().parent.parent / "web"
GRAPH = build(CORPUS)


@app.get("/up")
def up() -> dict:
    return {"status": "ok", "modules": len(CORPUS), "symbols": len(GRAPH.symbols)}


@app.get("/api/symbols")
def symbols() -> dict:
    return {
        "symbols": [
            {"fqn": s.fqn, "module": s.module, "name": s.name, "kind": s.kind,
             "lineno": s.lineno, "callers": len(GRAPH.callers.get(s.fqn, set()))}
            for s in sorted(GRAPH.symbols.values(), key=lambda s: s.fqn)
        ],
        "collisions": GRAPH.short_name_collisions(),
    }


@app.get("/api/impact")
def api_impact(fqn: str) -> dict:
    try:
        result = impact(GRAPH, fqn)
    except ResolutionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "target": result.target,
        "direct": result.direct,
        "transitive": result.transitive,
        "in_cycle": result.in_cycle,
        "depth": result.depth,
    }


@app.get("/api/source")
def source(module: str) -> dict:
    if module not in CORPUS:
        raise HTTPException(status_code=404, detail="no such module")
    return {"module": module, "source": CORPUS[module]}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (WEB / "index.html").read_text()
