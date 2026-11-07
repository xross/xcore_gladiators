"""Gladiators longitudinal results index (Python stdlib / SQLite).

Source of truth is immutable per-experiment JSON and REPORT.rst; this database
is a rebuildable catalogue for comparing experiments over time. It intentionally
indexes aggregates, never worker prompts, source code, transcripts or secrets.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path


def connect(db_path: Path) -> sqlite3.Connection:
    db_path = Path(db_path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS experiments (
            experiment_id TEXT PRIMARY KEY,
            started_utc TEXT,
            title TEXT,
            smoke INTEGER NOT NULL DEFAULT 0,
            reviewer_enabled INTEGER,
            config_sha256 TEXT,
            skills_revision TEXT,
            opencode_version TEXT,
            results_dir TEXT NOT NULL,
            report_path TEXT,
            planned_trials INTEGER,
            indexed_utc TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS trials (
            experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id) ON DELETE CASCADE,
            run_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            task_family TEXT,
            prompt_sha256 TEXT,
            prompt_specificity TEXT,
            worker_label TEXT,
            model_id TEXT NOT NULL,
            condition TEXT NOT NULL,
            repeat_index INTEGER,
            started_utc TEXT,
            verification_status TEXT,
            worker_exit_code INTEGER,
            worker_seconds REAL,
            verified_seconds REAL,
            input_tokens INTEGER,
            output_tokens INTEGER,
            reasoning_tokens INTEGER,
            cache_read_tokens INTEGER,
            cache_write_tokens INTEGER,
            estimated_usd REAL,
            estimated_ai_credits REAL,
            skill_call_count INTEGER,
            model_mismatch INTEGER,
            error TEXT,
            result_path TEXT NOT NULL,
            PRIMARY KEY (experiment_id, run_id)
        );
        CREATE TABLE IF NOT EXISTS reviews (
            experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id) ON DELETE CASCADE,
            task_id TEXT NOT NULL,
            reviewer_model TEXT,
            status TEXT,
            estimated_usd REAL,
            review_path TEXT NOT NULL,
            PRIMARY KEY (experiment_id, task_id)
        );
        CREATE INDEX IF NOT EXISTS idx_trials_task_model ON trials(task_id, model_id, condition);
        CREATE INDEX IF NOT EXISTS idx_trials_prompt ON trials(prompt_sha256);
        CREATE INDEX IF NOT EXISTS idx_trials_date ON trials(started_utc);
    """)
    return db


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def index_experiment(db: sqlite3.Connection, result_dir: Path) -> tuple[int, int]:
    """Idempotently index one experiment, including partially finished runs."""
    root = Path(result_dir).resolve()
    manifest_file = root / "experiment.json"
    if not manifest_file.is_file():
        raise FileNotFoundError(f"Missing experiment.json: {manifest_file}")
    manifest = _json(manifest_file)
    experiment_id = manifest.get("experiment_id") or root.name
    conf = manifest.get("report_config") or {}
    exp = conf.get("experiment") or {}
    options = manifest.get("run_options") or {}
    env = manifest.get("environment") or {}
    trial_count = review_count = 0
    with db:
        db.execute("""
            INSERT INTO experiments
            (experiment_id,started_utc,title,smoke,reviewer_enabled,config_sha256,
             skills_revision,opencode_version,results_dir,report_path,planned_trials,indexed_utc)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(experiment_id) DO UPDATE SET
              started_utc=excluded.started_utc,title=excluded.title,smoke=excluded.smoke,
              reviewer_enabled=excluded.reviewer_enabled,config_sha256=excluded.config_sha256,
              skills_revision=excluded.skills_revision,opencode_version=excluded.opencode_version,
              results_dir=excluded.results_dir,report_path=excluded.report_path,
              planned_trials=excluded.planned_trials,indexed_utc=excluded.indexed_utc
        """, (experiment_id, manifest.get("started_utc"), exp.get("name"),
              int(bool(options.get("smoke", False))),
              int(bool(options.get("review_enabled", exp.get("review_enabled", False)))),
              manifest.get("configuration_sha256"), env.get("xskills_revision"),
              env.get("opencode_version"), str(root),
              str(root / "REPORT.rst") if (root / "REPORT.rst").is_file() else None,
              len(manifest.get("run_order") or []), dt.datetime.now(dt.timezone.utc).isoformat()))
        for file in sorted((root / "runs").glob("*/result.json")):
            trial = _json(file)
            validation = trial.get("validation") or {}
            if validation.get("status") != "error":
                from verification.uart_heartbeat import validate_observations
                if validation.get("status") not in ("passed", "failed"):
                    raise ValueError("Only independently checked results or execution errors can be indexed")
                validate_observations(validation.get("observations"))
                if validation["observations"]["passed"] != (validation["status"] == "passed"):
                    raise ValueError("Verification status contradicts its independent evidence")
            usage = trial.get("usage") or {}
            tokens = usage.get("tokens") or {}
            invocation = trial.get("invocation") or {}
            db.execute("""
                INSERT INTO trials
                  (experiment_id,run_id,task_id,task_family,prompt_sha256,prompt_specificity,
                   worker_label,model_id,condition,repeat_index,started_utc,verification_status,
                   worker_exit_code,worker_seconds,verified_seconds,input_tokens,output_tokens,
                   reasoning_tokens,cache_read_tokens,cache_write_tokens,estimated_usd,
                   estimated_ai_credits,skill_call_count,model_mismatch,error,result_path)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(experiment_id,run_id) DO UPDATE SET
                  task_id=excluded.task_id,task_family=excluded.task_family,
                  prompt_sha256=excluded.prompt_sha256,prompt_specificity=excluded.prompt_specificity,
                  worker_label=excluded.worker_label,model_id=excluded.model_id,
                  condition=excluded.condition,repeat_index=excluded.repeat_index,
                  started_utc=excluded.started_utc,verification_status=excluded.verification_status,
                  worker_exit_code=excluded.worker_exit_code,worker_seconds=excluded.worker_seconds,
                  verified_seconds=excluded.verified_seconds,input_tokens=excluded.input_tokens,
                  output_tokens=excluded.output_tokens,reasoning_tokens=excluded.reasoning_tokens,
                  cache_read_tokens=excluded.cache_read_tokens,cache_write_tokens=excluded.cache_write_tokens,
                  estimated_usd=excluded.estimated_usd,estimated_ai_credits=excluded.estimated_ai_credits,
                  skill_call_count=excluded.skill_call_count,model_mismatch=excluded.model_mismatch,
                  error=excluded.error,result_path=excluded.result_path
            """, (experiment_id, trial["run_id"],trial.get("task_id", "unknown"),
                  trial.get("task_family"),trial.get("prompt_sha256"),trial.get("prompt_specificity"),
                  trial.get("model_label"),trial.get("model") or "unknown",trial.get("condition") or "unknown",
                  trial.get("repeat"),trial.get("started_utc"),
                  (trial.get("validation") or {}).get("status"),invocation.get("exit_code"),
                  invocation.get("elapsed_seconds"),trial.get("time_to_verified_success_s"),
                  tokens.get("input"),tokens.get("output"),tokens.get("reasoning"),
                  tokens.get("cache_read"),tokens.get("cache_write"),
                  usage.get("copilot_estimated_usd"),usage.get("copilot_estimated_ai_credits"),
                  len(trial.get("skill_calls") or []),int(bool(trial.get("requested_model_mismatch"))),
                  trial.get("error"),str(file)))
            trial_count += 1
        for file in sorted((root / "reviews").glob("*/review-result.json")):
            review = _json(file)
            db.execute("""
                INSERT INTO reviews(experiment_id,task_id,reviewer_model,status,estimated_usd,review_path)
                VALUES(?,?,?,?,?,?) ON CONFLICT(experiment_id,task_id) DO UPDATE SET
                  reviewer_model=excluded.reviewer_model,status=excluded.status,
                  estimated_usd=excluded.estimated_usd,review_path=excluded.review_path
            """, (experiment_id, review.get("task_id") or file.parent.name,
                  review.get("reviewer_model"),review.get("status"),
                  (review.get("usage") or {}).get("copilot_estimated_usd"),str(file)))
            review_count += 1
    return trial_count, review_count


def reindex(db: sqlite3.Connection, results_root: Path) -> tuple[int, int]:
    """Rebuild the index from archived runs (including archives predating SQLite)."""
    indexed = trials = 0
    for root in sorted(Path(results_root).glob("*/experiment.json")):
        count, _ = index_experiment(db, root.parent)
        indexed += 1
        trials += count
    return indexed, trials


def _fmt(value: object, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def history(db: sqlite3.Connection, limit: int = 20) -> list[str]:
    query = """
      SELECT e.experiment_id, e.started_utc, e.smoke, e.planned_trials,
             COUNT(t.run_id) as completed, SUM(CASE WHEN t.verification_status='passed' THEN 1 ELSE 0 END) as passes,
             ROUND(SUM(COALESCE(t.estimated_usd,0)),4) as worker_usd,
             (SELECT ROUND(SUM(COALESCE(v.estimated_usd,0)),4) FROM reviews v
              WHERE v.experiment_id=e.experiment_id) as referee_usd,
             e.report_path
      FROM experiments e LEFT JOIN trials t ON e.experiment_id=t.experiment_id
        AND t.verification_status IN ('passed','failed','error')
      GROUP BY e.experiment_id HAVING COUNT(t.run_id)>0 OR e.report_path IS NULL
      ORDER BY e.started_utc DESC, e.experiment_id DESC LIMIT ?
    """
    rows = db.execute(query, (limit,)).fetchall()
    if not rows:
        return ["No experiments indexed. Run a benchmark or use: python3 benchmark.py index"]
    out = ["Experiment ID               Date (UTC)           Mode     Done/plan Verified  Est. USD Report"]
    for row in rows:
        cost = (row["worker_usd"] or 0) + (row["referee_usd"] or 0)
        out.append(f"{row['experiment_id'][:27]:27} {(row['started_utc'] or '')[:19]:19} "
                   f"{'SMOKE' if row['smoke'] else 'full':8} {row['completed']:2}/{_fmt(row['planned_trials']):4} "
                   f"{row['passes']:3}       ${cost:8.4f} "
                   f"{row['report_path'] or '(pending)'}")
    return out


def trend(db: sqlite3.Connection, task: str | None = None, model: str | None = None,
          condition: str | None = None, limit: int = 40) -> list[str]:
    """Time-ordered individual trials; prompt hash prevents accidental comparisons."""
    filters, args = ["t.verification_status IN ('passed','failed','error')"], []
    if task:
        filters.append("t.task_id = ?")
        args.append(task)
    if model:
        filters.append("(t.model_id = ? OR t.worker_label = ?)")
        args.extend([model,model])
    if condition:
        filters.append("t.condition = ?")
        args.append(condition)
    where = (" WHERE " + " AND ".join(filters)) if filters else ""
    rows = db.execute(f"""
      SELECT t.*,e.started_utc AS experiment_date,e.smoke,e.skills_revision
      FROM trials t JOIN experiments e USING(experiment_id)
      {where} ORDER BY e.started_utc ASC,t.run_id ASC LIMIT ?
    """, (*args,limit)).fetchall()
    if not rows:
        return ["No trials match. Check the task/model/condition filters, or run 'index'."]
    lines = ["Date (UTC)          Task                     Model                        Condition    Status      Time(s)  Est. USD  Prompt hash   Experiment"]
    for r in rows:
        lines.append(f"{(r['started_utc'] or r['experiment_date'] or '')[:19]:19} "
                     f"{r['task_id'][:24]:24} {r['model_id'][:28]:28} "
                     f"{r['condition'][:12]:12} {(r['verification_status'] or '?')[:11]:11} "
                     f"{_fmt(r['worker_seconds'],1):>7}  {_fmt(r['estimated_usd'],4):>8} "
                     f"{(r['prompt_sha256'] or 'n/a')[:11]:11} {r['experiment_id']}")
    lines.append("Note: compare only identical prompt hashes and equivalent verifier/toolchain/skill revisions;"
                 " a provider may change a model behind an unchanged model ID.")
    return lines


def compare(db: sqlite3.Connection, task: str | None = None, model: str | None = None,
            condition: str | None = None) -> list[str]:
    filters, args = ["t.verification_status IN ('passed','failed','error')"], []
    if task:
        filters.append("t.task_id = ?")
        args.append(task)
    if model:
        filters.append("(t.model_id = ? OR t.worker_label = ?)")
        args.extend([model,model])
    if condition:
        filters.append("t.condition = ?")
        args.append(condition)
    where = (" WHERE " + " AND ".join(filters)) if filters else ""
    rows = db.execute(f"""
      SELECT t.task_id,t.model_id,t.condition,t.prompt_sha256,
             COUNT(*) as n, SUM(t.verification_status='passed') as passes,
              SUM(t.verification_status IN ('passed','failed')) as checked,
             ROUND(AVG(t.worker_seconds),1) as avg_worker_s,
             ROUND(AVG(t.estimated_usd),4) as avg_usd,
             MIN(e.started_utc) as first_run,MAX(e.started_utc) as last_run
      FROM trials t JOIN experiments e USING(experiment_id)
      {where} GROUP BY t.task_id,t.model_id,t.condition,t.prompt_sha256
      ORDER BY t.task_id,t.model_id,t.condition,t.prompt_sha256
    """, tuple(args)).fetchall()
    if not rows:
        return ["No comparable trials indexed."]
    out = ["Task                     Model                        Condition    N   Pass/checked   Avg(s) Est.USD Prompt hash First..last"]
    for r in rows:
        out.append(f"{r['task_id'][:24]:24} {r['model_id'][:28]:28} "
                   f"{r['condition'][:12]:12} {r['n']:3} "
                   f"{r['passes'] or 0:3}/{r['checked'] or 0:<8} "
                   f"{_fmt(r['avg_worker_s'],1):>6} {_fmt(r['avg_usd'],4):>7} "
                   f"{(r['prompt_sha256'] or 'n/a')[:11]:11} "
                   f"{(r['first_run'] or '')[:10]}..{(r['last_run'] or '')[:10]}")
    out.append("Failed and errored trials must not be counted as passing. "
               "Prompt hashes identify task wording; review verifier revisions before trend claims.")
    return out
