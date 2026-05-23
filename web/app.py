"""TradingAgents Web UI — FastAPI backend."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.llm_clients.model_catalog import MODEL_OPTIONS

app = FastAPI(title="TradingAgents Web UI")

_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

_running_tasks: Dict[str, Dict[str, Any]] = {}
_CUSTOM_PROVIDER_KEY = "custom-openai-compatible"


class AnalysisRequest(BaseModel):
    ticker: str
    analysis_date: str
    analysts: List[str]
    llm_provider: str
    quick_think_llm: str
    deep_think_llm: str
    research_depth: int = 1
    output_language: str = "English"
    backend_url: Optional[str] = None


@app.get("/", response_class=HTMLResponse)
async def index():
    return (_STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/providers")
async def get_providers():
    providers = []
    custom_backend_url = DEFAULT_CONFIG.get("backend_url")
    if custom_backend_url:
        providers.append({
            "key": _CUSTOM_PROVIDER_KEY,
            "label": "custom (OpenAI-compatible)",
            "backend_url": custom_backend_url,
            "quick_models": [{
                "label": f"{DEFAULT_CONFIG.get('quick_think_llm', 'custom')} (from .env)",
                "value": DEFAULT_CONFIG.get("quick_think_llm", "custom"),
            }],
            "deep_models": [{
                "label": f"{DEFAULT_CONFIG.get('deep_think_llm', 'custom')} (from .env)",
                "value": DEFAULT_CONFIG.get("deep_think_llm", "custom"),
            }],
        })
    for key, modes in MODEL_OPTIONS.items():
        if key.endswith("-cn"):
            continue
        providers.append({
            "key": key,
            "label": key,
            "backend_url": "",
            "quick_models": [{"label": label, "value": value} for label, value in modes["quick"]],
            "deep_models": [{"label": label, "value": value} for label, value in modes["deep"]],
        })
    return providers


@app.get("/api/defaults")
async def get_defaults():
    """Return current config defaults from .env / DEFAULT_CONFIG."""
    return {
        "llm_provider": _CUSTOM_PROVIDER_KEY if DEFAULT_CONFIG.get("backend_url") else DEFAULT_CONFIG.get("llm_provider", "openai"),
        "deep_think_llm": DEFAULT_CONFIG.get("deep_think_llm", ""),
        "quick_think_llm": DEFAULT_CONFIG.get("quick_think_llm", ""),
        "backend_url": DEFAULT_CONFIG.get("backend_url", ""),
        "output_language": DEFAULT_CONFIG.get("output_language", "English"),
        "max_debate_rounds": DEFAULT_CONFIG.get("max_debate_rounds", 1),
    }


@app.get("/api/history")
async def get_history():
    from tradingagents.agents.utils.rating import parse_rating

    results_dir = Path(DEFAULT_CONFIG["results_dir"])
    history = []
    if not results_dir.exists():
        return history

    for ticker_dir in sorted(results_dir.iterdir(), reverse=True):
        if not ticker_dir.is_dir():
            continue
        ticker = ticker_dir.name
        logs_dir = ticker_dir / "TradingAgentsStrategy_logs"
        if not logs_dir.exists():
            continue
        # Completed analyses
        for log_file in sorted(logs_dir.glob("full_states_log_*.json"), reverse=True):
            date_str = log_file.stem.replace("full_states_log_", "")
            rating = ""
            try:
                data = json.loads(log_file.read_text(encoding="utf-8"))
                decision_text = data.get("final_trade_decision", "")
                rating = parse_rating(decision_text) if decision_text else ""
            except Exception:
                pass
            history.append({
                "ticker": ticker,
                "date": date_str,
                "file": str(log_file),
                "created": datetime.fromtimestamp(log_file.stat().st_mtime).isoformat(),
                "status": "completed",
                "rating": rating,
            })
        # Partial (interrupted) analyses
        for partial_file in sorted(logs_dir.glob("partial_*.json"), reverse=True):
            date_str = partial_file.stem.replace("partial_", "")
            full_log = logs_dir / f"full_states_log_{date_str}.json"
            if not full_log.exists():
                history.append({
                    "ticker": ticker,
                    "date": date_str,
                    "file": str(partial_file),
                    "created": datetime.fromtimestamp(partial_file.stat().st_mtime).isoformat(),
                    "status": "interrupted",
                    "rating": "",
                })

    history.sort(key=lambda x: x["created"], reverse=True)
    return history


@app.get("/api/history/{ticker}/{date}")
async def get_history_detail(ticker: str, date: str):
    results_dir = Path(DEFAULT_CONFIG["results_dir"])
    log_file = results_dir / ticker / "TradingAgentsStrategy_logs" / f"full_states_log_{date}.json"
    if not log_file.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    data = json.loads(log_file.read_text(encoding="utf-8"))
    return data


@app.get("/api/analyze/active")
async def get_active_tasks():
    """Return all tasks that are still running or recently completed."""
    result = []
    for tid, task in _running_tasks.items():
        result.append({
            "task_id": tid,
            "status": task["status"],
            "request": task["request"],
            "progress_count": len(task["progress"]),
        })
    return result


@app.get("/api/analyze/resumable")
async def get_resumable_tasks():
    """Check for partial (interrupted) runs that have saved progress."""
    results_dir = Path(DEFAULT_CONFIG["results_dir"])
    resumable = []
    if not results_dir.exists():
        return resumable

    for ticker_dir in results_dir.iterdir():
        if not ticker_dir.is_dir():
            continue
        logs_dir = ticker_dir / "TradingAgentsStrategy_logs"
        if not logs_dir.exists():
            continue
        for partial in logs_dir.glob("partial_*.json"):
            date_str = partial.stem.replace("partial_", "")
            # Only show if there's no completed full log for the same date
            full_log = logs_dir / f"full_states_log_{date_str}.json"
            if not full_log.exists():
                try:
                    data = json.loads(partial.read_text(encoding="utf-8"))
                    sections_done = sum(1 for k in ("market_report", "sentiment_report",
                                                     "news_report", "fundamentals_report")
                                        if data.get(k))
                    resumable.append({
                        "ticker": ticker_dir.name,
                        "date": date_str,
                        "sections_done": sections_done,
                        "has_decision": bool(data.get("final_trade_decision")),
                    })
                except Exception:
                    resumable.append({"ticker": ticker_dir.name, "date": date_str,
                                      "sections_done": 0, "has_decision": False})

    return resumable


@app.post("/api/analyze")
async def start_analysis(req: AnalysisRequest):
    task_id = str(uuid.uuid4())
    _running_tasks[task_id] = {
        "status": "running",
        "progress": [],
        "request": req.model_dump(),
        "result": None,
        "error": None,
    }
    asyncio.create_task(_run_analysis(task_id, req))
    return {"task_id": task_id}


@app.get("/api/analyze/{task_id}/stream")
async def stream_progress(task_id: str, last_index: int = 0):
    if task_id not in _running_tasks:
        raise HTTPException(status_code=404, detail="Task not found")

    async def event_generator():
        last_idx = last_index
        while True:
            task = _running_tasks.get(task_id)
            if task is None:
                break

            progress = task["progress"]
            while last_idx < len(progress):
                yield f"data: {json.dumps(progress[last_idx])}\n\n"
                last_idx += 1

            if task["status"] in ("completed", "error"):
                final = {"type": "done", "status": task["status"]}
                if task["error"]:
                    final["error"] = task["error"]
                if task["result"]:
                    final["decision"] = task["result"]
                yield f"data: {json.dumps(final)}\n\n"
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/analyze/{task_id}")
async def get_task_status(task_id: str):
    if task_id not in _running_tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    task = _running_tasks[task_id]
    return {
        "status": task["status"],
        "error": task["error"],
        "result": task["result"],
    }


async def _run_analysis(task_id: str, req: AnalysisRequest):
    task = _running_tasks[task_id]

    def emit(msg_type: str, content: str, **extra):
        event = {"type": msg_type, "content": content, "time": datetime.now().strftime("%H:%M:%S")}
        event.update(extra)
        task["progress"].append(event)

    try:
        emit("status", f"Starting analysis for {req.ticker} on {req.analysis_date}")

        config = DEFAULT_CONFIG.copy()
        config["max_debate_rounds"] = req.research_depth
        config["max_risk_discuss_rounds"] = req.research_depth
        config["quick_think_llm"] = req.quick_think_llm
        config["deep_think_llm"] = req.deep_think_llm
        config["llm_provider"] = "openai" if req.llm_provider == _CUSTOM_PROVIDER_KEY else req.llm_provider
        config["backend_url"] = req.backend_url or None
        config["output_language"] = req.output_language

        emit("status", "Initializing trading agents graph...")

        from tradingagents.graph.trading_graph import TradingAgentsGraph
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

        def run_sync():
            graph_obj = TradingAgentsGraph(
                selected_analysts=req.analysts,
                config=config,
                debug=True,
            )

            emit("status", "Graph initialized. Starting streaming execution...")

            init_state = graph_obj.propagator.create_initial_state(
                req.ticker, req.analysis_date
            )
            args = graph_obj.propagator.get_graph_args()

            # Incremental save directory for crash recovery
            save_dir = Path(config["results_dir"]) / req.ticker / "TradingAgentsStrategy_logs"
            save_dir.mkdir(parents=True, exist_ok=True)
            partial_file = save_dir / f"partial_{req.analysis_date}.json"

            trace = []
            partial_state = {}

            for chunk in graph_obj.graph.stream(init_state, **args):
                trace.append(chunk)
                partial_state.update(chunk)

                # Save partial state after each node for crash recovery
                _save_partial_state(partial_file, partial_state, req.ticker, req.analysis_date)

                for message in chunk.get("messages", []):
                    content = getattr(message, "content", None)
                    if isinstance(message, AIMessage):
                        text = _extract_text(content)
                        if text:
                            emit("agent", text[:500])
                        if hasattr(message, "tool_calls") and message.tool_calls:
                            for tc in message.tool_calls:
                                name = tc["name"] if isinstance(tc, dict) else tc.name
                                tc_args = tc["args"] if isinstance(tc, dict) else tc.args
                                args_str = str(tc_args)[:200]
                                emit("tool_call", f"{name}({args_str})")
                    elif isinstance(message, ToolMessage):
                        text = _extract_text(content)
                        if text:
                            emit("tool_result", text[:300])
                    elif isinstance(message, HumanMessage):
                        text = _extract_text(content)
                        if text and text.strip() != "Continue":
                            emit("human", text[:200])

                if chunk.get("market_report"):
                    emit("report", "Market report generated", section="market")
                if chunk.get("sentiment_report"):
                    emit("report", "Sentiment report generated", section="sentiment")
                if chunk.get("news_report"):
                    emit("report", "News report generated", section="news")
                if chunk.get("fundamentals_report"):
                    emit("report", "Fundamentals report generated", section="fundamentals")

                if chunk.get("investment_debate_state"):
                    ds = chunk["investment_debate_state"]
                    if ds.get("bull_history"):
                        emit("agent_status", "Bull Researcher completed argument", agent="bull")
                    if ds.get("bear_history"):
                        emit("agent_status", "Bear Researcher completed argument", agent="bear")
                    if ds.get("judge_decision"):
                        emit("agent_status", "Research Manager made decision", agent="research_manager")

                if chunk.get("trader_investment_plan"):
                    emit("agent_status", "Trader completed investment plan", agent="trader")

                if chunk.get("risk_debate_state"):
                    rs = chunk["risk_debate_state"]
                    if rs.get("aggressive_history"):
                        emit("agent_status", "Aggressive Analyst completed", agent="aggressive")
                    if rs.get("conservative_history"):
                        emit("agent_status", "Conservative Analyst completed", agent="conservative")
                    if rs.get("neutral_history"):
                        emit("agent_status", "Neutral Analyst completed", agent="neutral")
                    if rs.get("judge_decision"):
                        emit("agent_status", "Portfolio Manager made final decision", agent="portfolio_manager")

                if chunk.get("final_trade_decision"):
                    emit("status", "Final trade decision received")

            # Merge all chunks into final state
            final_state = {}
            for c in trace:
                final_state.update(c)

            # Save final results to disk
            graph_obj.ticker = req.ticker
            graph_obj._log_state(req.analysis_date, final_state)

            # Remove partial file on success
            if partial_file.exists():
                partial_file.unlink()

            decision = graph_obj.process_signal(final_state.get("final_trade_decision", ""))
            return final_state, decision

        loop = asyncio.get_event_loop()
        final_state, decision = await loop.run_in_executor(None, run_sync)

        emit("status", f"Analysis complete. Decision: {decision}")
        task["result"] = decision
        task["status"] = "completed"

    except Exception as e:
        import traceback
        emit("error", f"{str(e)}\n{traceback.format_exc()}")
        task["error"] = str(e)
        task["status"] = "error"


def _extract_text(content) -> Optional[str]:
    if content is None:
        return None
    if isinstance(content, str):
        return content.strip() if content.strip() else None
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif isinstance(item, str):
                parts.append(item)
        result = " ".join(parts).strip()
        return result if result else None
    return str(content).strip() or None


def _save_partial_state(partial_file: Path, state: dict, ticker: str, date: str):
    """Save serializable parts of the state to disk for crash recovery."""
    serializable = {
        "company_of_interest": ticker,
        "trade_date": date,
    }
    for key in ("market_report", "sentiment_report", "news_report",
                "fundamentals_report", "trader_investment_plan", "final_trade_decision",
                "investment_plan"):
        val = state.get(key)
        if val and isinstance(val, str):
            serializable[key] = val

    if state.get("investment_debate_state"):
        ds = state["investment_debate_state"]
        serializable["investment_debate_state"] = {
            k: ds.get(k, "") for k in ("bull_history", "bear_history", "history",
                                        "current_response", "judge_decision")
        }
    if state.get("risk_debate_state"):
        rs = state["risk_debate_state"]
        serializable["risk_debate_state"] = {
            k: rs.get(k, "") for k in ("aggressive_history", "conservative_history",
                                        "neutral_history", "history", "judge_decision")
        }

    try:
        partial_file.write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def main():
    import uvicorn
    port = int(os.environ.get("TRADINGAGENTS_WEB_PORT", "8888"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
