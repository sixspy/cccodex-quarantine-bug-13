"""超时晋升红例：甲路（不可变快照）落地前全部应失败。

覆盖题目五条挂起红线：
1. 无晋升落点、超时不掉仍算挂起；
2. 只有晋升徽标没有落点行仍算挂起；
3. 脚手架残留（meta 假芯片）不得签字；
4. 指纹不合还放行仍算挂起；
5. 双人同抢皆成仍算挂起（串行晋升钉点唯一）。
另贯通：详情 / 回炉 / 对账 / 设置四块引用同一套晋升落点。
"""
from __future__ import annotations

import threading

import pytest

from app.services import captures, quarantine, review, rework
from app.services import timeout_promotion as tp
from app.services.errors import ServiceError


def _make_timeout_job(db_path: str) -> dict:
    """造一个 入舱 -> 驳回回炉 -> 执行中超时 的回炉任务。"""
    batches = captures.list_batches(db_path)
    batch = next(b for b in batches if b["status"] != "已关闭")
    open_bin = next(b for b in quarantine.list_bins(db_path) if b["status"] == "开放")
    detail = captures.get_batch(batch["id"], db_path)
    bad = next(i for i in detail["items"] if i["quality"] != "合格" and i["status"] == "待检")
    row = quarantine.enter_item(bad["id"], open_bin["id"], "超时晋升红例", db_path)
    ticket = next(
        t for t in review.list_tickets(db_path)
        if t["row_id"] == row["id"] and t["decision"] == "待决"
    )
    review.decide(ticket["id"], "驳回回炉", "tester", "回炉", db_path)
    job = next(j for j in rework.list_jobs(db_path) if j["row_id"] == row["id"])
    from app.db import get_db
    with get_db(db_path) as conn:
        conn.execute(
            "UPDATE rework_jobs SET status='执行中', started_at=datetime('now') WHERE id=?",
            (job["id"],),
        )
    timed = rework.mark_timeout(job["id"], db_path=db_path)
    return timed


# ---------- 段一：超时掉档，脏件不再悬挂 ----------

def test_timeout_moves_job_to_failed_and_row_back_to_pending(db_path):
    job = _make_timeout_job(db_path)
    assert job["status"] == "失败"
    assert "超时" in job["message"]
    rows = {r["id"]: r for r in quarantine.list_rows(db_path=db_path)}
    assert rows[job["row_id"]]["status"] == "待复核"
    # 回炉列表带超时档，且尚未挂晋升快照
    listed = next(j for j in rework.list_jobs(db_path) if j["id"] == job["id"])
    assert listed["timeout_stage"] == "超时失败"
    assert listed["promotion_snapshot_id"] is None


def test_running_job_can_be_promoted_only_after_timeout(db_path):
    _make_timeout_job(db_path)
    # 未超时的排队任务不许晋升：自建批次造一个干净的入舱回炉任务
    with pytest.raises(ServiceError):
        batch = captures.create_batch("红例源", "未超时晋升拦截", db_path)
        captures.add_item(batch["id"], "sku-red-1", "{}", "缺字段", db_path)
        open_bin = next(b for b in quarantine.list_bins(db_path) if b["status"] == "开放")
        detail = captures.get_batch(batch["id"], db_path)
        bad = next(i for i in detail["items"] if i["status"] == "待检")
        row2 = quarantine.enter_item(bad["id"], open_bin["id"], "红例二", db_path)
        j2 = rework.enqueue_from_row(row2["id"], db_path)
        tp.promote_timeout(j2["id"], actor="admin", db_path=db_path)


# ---------- 段二：晋升提交一次写定（快照+指纹），失败吐白话 ----------

def test_promote_writes_snapshot_row_with_fingerprint_and_token(db_path):
    job = _make_timeout_job(db_path)
    out = tp.promote_timeout(job["id"], actor="admin", db_path=db_path)
    assert out["job_id"] == job["id"]
    assert out["fingerprint"]
    assert out["promote_token"]
    assert out["snapshot_job_status"] == "失败"
    snap = tp.get_snapshot(out["snapshot_id"], db_path=db_path)
    assert snap["job_id"] == job["id"]
    assert snap["fingerprint"] == out["fingerprint"]
    assert snap["actor"] == "admin"


def test_promote_is_idempotent_second_call_returns_plain_error(db_path):
    job = _make_timeout_job(db_path)
    tp.promote_timeout(job["id"], actor="admin", db_path=db_path)
    with pytest.raises(ServiceError) as exc:
        tp.promote_timeout(job["id"], actor="admin", db_path=db_path)
    # 白话错误，不抛裸 sqlite 异常
    assert "已晋升" in exc.value.message or "重复" in exc.value.message


def test_snapshot_is_immutable_after_later_job_mutation(db_path):
    job = _make_timeout_job(db_path)
    out = tp.promote_timeout(job["id"], actor="admin", db_path=db_path)
    before = tp.get_snapshot(out["snapshot_id"], db_path=db_path)
    # 事后篡改任务消息（模拟现行详情变化）
    from app.db import get_db
    with get_db(db_path) as conn:
        conn.execute("UPDATE rework_jobs SET message=? WHERE id=?", ("事后被改", job["id"]))
    after = tp.get_snapshot(out["snapshot_id"], db_path=db_path)
    assert after["snapshot_message"] == before["snapshot_message"]
    assert after["fingerprint"] == before["fingerprint"]


# ---------- 段三：指纹不合不放行（闸门） ----------

def test_gate_blocks_pipeline_when_fingerprint_mismatch(db_path):
    from app.db import get_db
    job = _make_timeout_job(db_path)
    tp.promote_timeout(job["id"], actor="admin", db_path=db_path)
    assert tp.gate_allows(db_path) is True
    # 拧花当刻任务键（attempt 参与指纹），快照指纹即不合
    with get_db(db_path) as conn:
        conn.execute(
            "UPDATE rework_jobs SET attempt=attempt+10 WHERE id=?", (job["id"],)
        )
    assert tp.gate_allows(db_path) is False
    with pytest.raises(ServiceError):
        tp.require_gate(db_path)
    # 继续清洗被拦：回炉执行入口过闸失败
    with pytest.raises(ServiceError):
        rework.run_job(job["id"], succeed=True, db_path=db_path)


def test_gate_blocks_when_promotion_landed_but_row_missing(db_path):
    from app.db import get_db
    job = _make_timeout_job(db_path)
    out = tp.promote_timeout(job["id"], actor="admin", db_path=db_path)
    with get_db(db_path) as conn:
        conn.execute("DELETE FROM timeout_promotions WHERE id=?", (out["snapshot_id"],))
    # 徽标还亮着（任务上 promotion_id 钉点），但落点行没了 -> 仍挂起
    assert tp.gate_allows(db_path) is False


# ---------- 段四：双人并发同抢，只成一个 ----------

def test_concurrent_promote_only_one_wins(db_path):
    job = _make_timeout_job(db_path)
    results: list[Exception | dict] = []

    def fire(actor: str) -> None:
        try:
            results.append(tp.promote_timeout(job["id"], actor=actor, db_path=db_path))
        except Exception as exc:  # noqa: BLE001
            results.append(exc)

    t1 = threading.Thread(target=fire, args=("keeper-a",))
    t2 = threading.Thread(target=fire, args=("keeper-b",))
    t1.start(); t2.start(); t1.join(); t2.join()
    oks = [r for r in results if isinstance(r, dict)]
    errs = [r for r in results if isinstance(r, Exception)]
    assert len(oks) == 1
    assert len(errs) == 1
    from app.db import get_db
    with get_db(db_path) as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM timeout_promotions WHERE job_id=?", (job["id"],)
        ).fetchone()["c"]
    assert n == 1


# ---------- 段五：四块贯通，只读同一套快照引用 ----------

def test_detail_rework_reconcile_settings_share_one_promotion_ref(db_path):
    job = _make_timeout_job(db_path)
    out = tp.promote_timeout(job["id"], actor="admin", db_path=db_path)

    # 详情列表（批次条目侧）亮晋升徽标，引用快照号
    detail = tp.list_promotions(db_path)
    line = next(x for x in detail if x["job_id"] == job["id"])
    assert line["badge"] == "已晋升"
    assert line["snapshot_id"] == out["snapshot_id"]
    assert line["fingerprint"] == out["fingerprint"]

    # 回炉侧亮超时档，且带同一快照号
    jobs = rework.list_jobs(db_path)
    j = next(x for x in jobs if x["id"] == job["id"])
    assert j["timeout_stage"] == "超时失败"
    assert j["promotion_snapshot_id"] == out["snapshot_id"]

    # 对账卡挂超时摘要，数字与落点行一致
    summary = tp.timeout_summary(db_path)
    assert summary["timeout_total"] >= 1
    assert summary["promoted"] >= 1
    assert summary["hanging"] == summary["timeout_total"] - summary["promoted"]

    # 设置页只展晋升口径（甲路快照口径），不含乙路账册/双写字段
    spec = tp.settings_spec(db_path)
    assert spec["mode"] == "snapshot"
    assert "cutover_book" not in str(spec)
    assert "live_flag" not in str(spec)
    assert any(s["key"] == "rework_timeout_seconds" for s in spec["settings"])


def test_no_scaffold_meta_chips_remain(db_path):
    """脚手架残留不得签字：meta 里不再有 timeout_promote_* 假键。"""
    from app.db import get_db
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT key FROM meta WHERE key LIKE 'timeout_promote%'"
        ).fetchall()
    assert rows == []
    # 旧脚手架模块应已移除
    import importlib
    for name in (
        "app.services.timeout_promote",
        "app.services.timeout_promote_cutover",
        "app.services.timeout_promote_dualwrite",
        "app.services.timeout_promote_audit",
    ):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(name)
