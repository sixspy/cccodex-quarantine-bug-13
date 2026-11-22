"""超时晋升（甲路：不可变快照）。

工程形态择定：**甲路**。超时当刻把任务键与晋升指纹封进只读快照
（timeout_promotions），详情/回炉/对账/设置四块一律只读该快照引用；
不开乙路独立账册、不双写。

- mark_timeout：执行中的回炉任务超时 -> 失败档，脏件退回待复核，不再悬挂；
- promote_timeout：对超时任务一次性写定快照（UNIQUE(job_id) 串行同抢），
  指纹由当刻任务键派生，事后改任务不动快照；
- gate_allows：徽标（rework_jobs.promotion_id）与超时档、快照指纹拧花即拦，
  继续清洗（回炉执行）前必须过闸。
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Any

from app.db import get_db
from app.services import audit as audit_svc
from app.services import settings as settings_svc
from app.services.errors import ServiceError

MODE = "snapshot"
TIMEOUT_STAGE = "超时失败"
BADGE_PROMOTED = "已晋升"
BADGE_NONE = "未晋升"


# ---------------- 指纹与令牌 ----------------

def _fingerprint(job: Any) -> str:
    """晋升指纹：由超时当刻任务键与状态字段派生，事后任何字段被改即不合。"""
    raw = "|".join(
        str(x)
        for x in (
            job["id"],
            job["job_code"],
            job["row_id"],
            job["status"],
            job["attempt"],
            job["timed_out_at"],
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _recompute(job: Any) -> str:
    return _fingerprint(job)


# ---------------- 段一：超时掉档 ----------------

def mark_timeout(job_id: int, db_path: str | None = None) -> dict:
    """执行中任务超时：落失败档，脏件退回待复核，钉超时当刻时间。"""
    with get_db(db_path) as conn:
        job = conn.execute("SELECT * FROM rework_jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            raise ServiceError("回炉任务不存在", 404)
        if job["status"] != "执行中":
            raise ServiceError("只有执行中的任务可判超时")
        conn.execute(
            """
            UPDATE rework_jobs
            SET status='失败', finished_at=datetime('now'),
                timed_out_at=COALESCE(timed_out_at, datetime('now')),
                message='回炉抓取超时，已落失败档'
            WHERE id=?
            """,
            (job_id,),
        )
        # 脏件不再悬挂：隔离行退回待复核，条目退回入舱，等待重新处置
        conn.execute(
            "UPDATE quarantine_rows SET status='待复核' WHERE id=?",
            (job["row_id"],),
        )
        conn.execute(
            """
            UPDATE capture_items SET status='入舱'
            WHERE id=(SELECT item_id FROM quarantine_rows WHERE id=?)
            """,
            (job["row_id"],),
        )
        out = conn.execute("SELECT * FROM rework_jobs WHERE id=?", (job_id,)).fetchone()
        data = dict(out)
    audit_svc.write_event("回炉超时", "rework_job", job_id, data["job_code"], db_path=db_path)
    return data


# ---------------- 段二：一次性晋升，写定只读快照 ----------------

def promote_timeout(job_id: int, actor: str = "admin", db_path: str | None = None) -> dict:
    """对超时任务做晋升：一次写定快照；重复晋升/并发同抢吐白话错误。"""
    actor = (actor or "admin").strip() or "admin"
    with get_db(db_path) as conn:
        job = conn.execute("SELECT * FROM rework_jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            raise ServiceError("回炉任务不存在", 404)
        if not job["timed_out_at"] or job["status"] != "失败":
            raise ServiceError("该任务未超时，不能晋升")
        dup = conn.execute(
            "SELECT id FROM timeout_promotions WHERE job_id=?", (job_id,)
        ).fetchone()
        if dup or job["promotion_id"]:
            raise ServiceError("该超时任务已晋升，请勿重复提交")
        item = conn.execute(
            """
            SELECT i.item_key FROM quarantine_rows r
            JOIN capture_items i ON i.id=r.item_id
            WHERE r.id=?
            """,
            (job["row_id"],),
        ).fetchone()
        token = f"PTO-{uuid.uuid4().hex[:16]}"
        fp = _fingerprint(job)
        try:
            cur = conn.execute(
                """
                INSERT INTO timeout_promotions(
                  job_id, promote_token, fingerprint,
                  snapshot_job_code, snapshot_job_status, snapshot_attempt,
                  snapshot_message, snapshot_item_key, snapshot_timed_out_at, actor
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    job["id"],
                    token,
                    fp,
                    job["job_code"],
                    job["status"],
                    job["attempt"],
                    job["message"],
                    item["item_key"] if item else "",
                    job["timed_out_at"],
                    actor,
                ),
            )
        except Exception:
            # UNIQUE(job_id) 兜底并发同抢：双写覆盖在此被数据库拒掉
            raise ServiceError("该超时任务已被其他仓管晋升，本次提交未生效", 409)
        snapshot_id = cur.lastrowid
        conn.execute(
            "UPDATE rework_jobs SET promotion_id=? WHERE id=?",
            (snapshot_id, job["id"]),
        )
        snap = conn.execute(
            "SELECT * FROM timeout_promotions WHERE id=?", (snapshot_id,)
        ).fetchone()
        data = dict(snap)
    audit_svc.write_event(
        "超时晋升",
        "timeout_promotion",
        snapshot_id,
        f"{data['snapshot_job_code']} token={token}",
        actor=actor,
        db_path=db_path,
    )
    return {
        "snapshot_id": snapshot_id,
        "job_id": job["id"],
        "promote_token": token,
        "fingerprint": fp,
        "snapshot_job_status": data["snapshot_job_status"],
        "actor": actor,
    }


def get_snapshot(snapshot_id: int, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM timeout_promotions WHERE id=?", (snapshot_id,)
        ).fetchone()
        if not row:
            raise ServiceError("晋升快照不存在", 404)
        data = dict(row)
        data["snapshot_id"] = data["id"]
        return data


# ---------------- 段三：详情列表 / 回炉侧引用 ----------------

def list_promotions(db_path: str | None = None) -> list[dict]:
    """详情侧晋升清单：每个超时任务一行，亮晋升徽标并挂快照引用。"""
    with get_db(db_path) as conn:
        rows = conn.execute(
            """
            SELECT j.id AS job_id, j.job_code, j.status, j.timed_out_at,
                   j.promotion_id, i.item_key, b.bin_code,
                   p.id AS snapshot_id, p.promote_token, p.fingerprint,
                   p.snapshot_job_status, p.actor
            FROM rework_jobs j
            JOIN quarantine_rows r ON r.id=j.row_id
            JOIN capture_items i ON i.id=r.item_id
            JOIN quarantine_bins b ON b.id=r.bin_id
            LEFT JOIN timeout_promotions p ON p.job_id=j.id
            WHERE j.timed_out_at IS NOT NULL
            ORDER BY j.id DESC
            """
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["badge"] = BADGE_PROMOTED if d["snapshot_id"] else BADGE_NONE
            d["timeout_stage"] = TIMEOUT_STAGE
            out.append(d)
        return out


def item_promotion_map(batch_id: int, db_path: str | None = None) -> dict[int, dict]:
    """批次条目 -> 晋升徽标/快照号，供详情列表跟同一套快照引用走。"""
    with get_db(db_path) as conn:
        rows = conn.execute(
            """
            SELECT i.id AS item_id, p.id AS snapshot_id, p.fingerprint,
                   p.promote_token, j.timed_out_at
            FROM capture_items i
            JOIN quarantine_rows r ON r.item_id=i.id
            JOIN rework_jobs j ON j.row_id=r.id
            LEFT JOIN timeout_promotions p ON p.job_id=j.id
            WHERE i.batch_id=? AND j.timed_out_at IS NOT NULL
            """,
            (batch_id,),
        ).fetchall()
        return {
            r["item_id"]: {
                "promotion_badge": BADGE_PROMOTED if r["snapshot_id"] else BADGE_NONE,
                "promotion_snapshot_id": r["snapshot_id"],
                "promotion_fingerprint": r["fingerprint"],
                "promote_token": r["promote_token"],
                "timeout_stage": TIMEOUT_STAGE if r["timed_out_at"] else "",
            }
            for r in rows
        }


# ---------------- 段四：闸门（徽标 vs 超时档 vs 落点 拧花即拦） ----------------

def _violations(conn) -> list[str]:
    problems: list[str] = []
    # 1) 任务钉了 promotion_id 但快照行没了（只有徽标没有落点）
    dangling = conn.execute(
        """
        SELECT j.id FROM rework_jobs j
        LEFT JOIN timeout_promotions p ON p.id=j.promotion_id
        WHERE j.promotion_id IS NOT NULL AND p.id IS NULL
        """
    ).fetchall()
    if dangling:
        problems.append("存在晋升徽标但缺落点快照行")
    # 2) 快照存在但任务没回钉（落点行悬空）
    unlinked = conn.execute(
        """
        SELECT p.id FROM timeout_promotions p
        LEFT JOIN rework_jobs j ON j.promotion_id=p.id
        WHERE j.id IS NULL
        """
    ).fetchall()
    if unlinked:
        problems.append("存在落点快照但任务未挂晋升徽标")
    # 3) 指纹不合：重算当刻任务键与快照指纹比对
    for p in conn.execute("SELECT * FROM timeout_promotions").fetchall():
        job = conn.execute("SELECT * FROM rework_jobs WHERE id=?", (p["job_id"],)).fetchone()
        if not job:
            problems.append(f"快照{p['id']}引用的任务已消失")
            continue
        if _recompute(job) != p["fingerprint"]:
            problems.append(f"快照{p['id']}指纹与当刻任务不合")
    # 4) 超时未晋升仍算挂起
    hanging = conn.execute(
        """
        SELECT COUNT(*) AS c FROM rework_jobs
        WHERE timed_out_at IS NOT NULL AND promotion_id IS NULL
        """
    ).fetchone()["c"]
    if hanging:
        problems.append(f"尚有{hanging}个超时任务未晋升，脏件悬挂")
    return problems


def gate_allows(db_path: str | None = None) -> bool:
    with get_db(db_path) as conn:
        return not _violations(conn)


def require_gate(db_path: str | None = None) -> None:
    with get_db(db_path) as conn:
        problems = _violations(conn)
    if problems:
        raise ServiceError("晋升闸门未过：" + "；".join(problems), 409)


# ---------------- 段五：对账超时摘要 ----------------

def timeout_summary(db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        timeout_total = conn.execute(
            "SELECT COUNT(*) AS c FROM rework_jobs WHERE timed_out_at IS NOT NULL"
        ).fetchone()["c"]
        promoted = conn.execute(
            "SELECT COUNT(*) AS c FROM timeout_promotions"
        ).fetchone()["c"]
        hanging_rows = conn.execute(
            """
            SELECT j.id, j.job_code, i.item_key
            FROM rework_jobs j
            JOIN quarantine_rows r ON r.id=j.row_id
            JOIN capture_items i ON i.id=r.item_id
            WHERE j.timed_out_at IS NOT NULL AND j.promotion_id IS NULL
            ORDER BY j.id
            """
        ).fetchall()
    hanging = timeout_total - promoted
    return {
        "mode": MODE,
        "timeout_stage": TIMEOUT_STAGE,
        "timeout_total": timeout_total,
        "promoted": promoted,
        "hanging": hanging,
        "gate_open": hanging == 0 and gate_allows(db_path),
        "hanging_jobs": [dict(r) for r in hanging_rows],
        "summary_text": (
            f"超时档{TIMEOUT_STAGE}：超时{timeout_total} / 已晋升{promoted} / "
            f"悬挂{hanging}"
        ),
    }


# ---------------- 段六：设置页只展晋升口径（甲路） ----------------

def settings_spec(db_path: str | None = None) -> dict:
    """设置页只展甲路快照晋升口径；不暴露乙路账册/双写任何字段。"""
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT key, value, label FROM lab_settings ORDER BY key"
        ).fetchall()
    settings = [dict(r) for r in rows]
    return {
        "mode": MODE,
        "mode_label": "不可变快照（超时当刻任务键+晋升指纹，回炉只读快照）",
        "read_ref": "timeout_promotions",
        "timeout_stage": TIMEOUT_STAGE,
        "promoted_badge": BADGE_PROMOTED,
        "settings": settings,
        "timeout_policy": {
            "rework_max_attempt": settings_svc.get_value(
                "rework_max_attempt", "3", db_path
            ),
            "rework_timeout_seconds": settings_svc.get_value(
                "rework_timeout_seconds", "300", db_path
            ),
        },
    }
