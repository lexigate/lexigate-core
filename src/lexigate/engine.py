# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LexiGate contributors
"""Answer engine: question -> NLU -> answer paths -> Answer.

Path 1 (`FixedQueryPath`, implemented here) binds intents from the semantic_map to fixed, parameterized
queries over the `sem` relations. Nothing else is executed.

Path 2 (`lexigate.generated.GeneratedSQLPath`) is added after path 1 when an LLM provider is configured
(see `lexigate.llm`); without a key it is absent. Any other object implementing `AnswerPath` can be appended to
`Engine.paths` or published under the entry-point group ``lexigate.answer_paths``. The engine tries paths in
order and returns the first answer. A path-2 answer calls `Answer.mark_generated()`, which adds the soft footer
and the "show me the SQL" suggestion described in the README (Dual-path answering). The SQL and the semantic
definitions behind it stay on the server and are returned only when the user asks for them in the same session.
"""
from __future__ import annotations

import os
import threading
import uuid
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from importlib.metadata import entry_points
from typing import Any, Callable, Protocol
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

from lexigate.nlu import NLU, Parsed, RulesNLU

GENERATED_FOOTER_JA = "※ AI が作成した回答です。念のため内容をご確認ください"
GENERATED_FOOTER_EN = "AI-generated — please double-check."
CONFIRM_HINT_JA = "確認：さっきの SQL と根拠は？"
CLARIFY_JA = "ご質問の内容を特定できませんでした。このデモでは次のような定型の質問に答えられます。"
HELP_JA = ("LexiGate クイックスタート（デモデータ）では、承認済み・未払いの出張精算の残高、勤怠未確定の案件と確認担当、"
           "精算書の明細・承認・支払の証跡を照会できます。時点（先月末／X月末／X月X日時点）を添えて質問してください。")
STARTER_HINTS = ["先月末時点の未払い出張精算はいくら？", "勤怠未確定は何名？", "C1001 の明細を見せて"]


@dataclass
class Answer:
    path: str                                 # "fixed" | "clarify" | "help" | (plug-ins) "generated"
    answer_ja: str
    intent: str | None = None
    as_of: dict | None = None
    data: dict = field(default_factory=dict)
    hints: list[str] = field(default_factory=list)
    footer_ja: str | None = None
    ai_generated: bool = False
    semantic_version: str | None = None
    data_label: str | None = None
    nlu: dict = field(default_factory=dict)
    query_id: str | None = None
    session_id: str | None = None
    sql_available: bool = False
    rationale: dict | None = field(default=None, repr=False)     # server-side only (SQL + definitions)

    def mark_generated(self) -> "Answer":
        """Path 2: soft red footer + one extra suggestion to reveal the SQL and its basis."""
        self.ai_generated, self.path, self.sql_available = True, "generated", True
        self.footer_ja = GENERATED_FOOTER_JA
        if CONFIRM_HINT_JA not in self.hints:
            self.hints = self.hints[:2] + [CONFIRM_HINT_JA]
        return self

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("rationale", None)                 # never sent with the answer
        return d


class AnswerPath(Protocol):
    name: str

    def answer(self, parsed: Parsed, ctx: "Context") -> Answer | None:
        """Return an Answer, or None to let the next path try."""


@dataclass
class Context:
    semantic_map: dict
    base: date
    dsn: str
    tz: ZoneInfo
    generated_enabled: bool = False

    def connect(self) -> psycopg.Connection:
        cx = psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row)
        cx.execute("SET default_transaction_read_only = on")
        cx.execute("SET statement_timeout = 5000")
        return cx


# ------------------------------------------------------------------ as-of
def resolve_as_of(as_of: dict, base: date, tz: ZoneInfo) -> dict:
    """Exclusive cutoff per spec: previous month-end = 1st of the base month 00:00; date X = X+1 00:00 (map timezone)."""
    mode = as_of["mode"]
    if mode == "previous_month_end":
        cut = base.replace(day=1)
        last = cut - timedelta(days=1)
        label = f"{last.year}年{last.month}月末時点"
    elif mode == "month_end":
        y, m = map(int, as_of["month"].split("-"))
        cut = date(y + (m == 12), m % 12 + 1, 1)
        label = f"{y}年{m}月末時点"
    elif mode == "date":
        d = date.fromisoformat(as_of["date"])
        cut = d + timedelta(days=1)
        label = f"{d.year}年{d.month}月{d.day}日時点"
    else:
        raise ValueError(f"unknown as_of mode {mode}")
    if cut > base:
        raise ValueError("future")
    return {**as_of, "label_ja": label, "cutoff": datetime.combine(cut, time(0), tz).isoformat()}


def yen(n: int) -> str:
    return f"-¥{-n:,}" if n < 0 else f"¥{n:,}"


# ------------------------------------------------------------------ path 1: fixed queries
Handler = Callable[[psycopg.Connection, Parsed, dict, "Context"], Answer]


class FixedQueryPath:
    """Intent -> fixed parameterized SQL over relations declared in the semantic_map."""

    name = "fixed"
    #: intent -> (relations the query reads, handler). Relations must exist in semantic_map.relations.
    registry: dict[str, tuple[list[str], Handler]] = {}

    def __init__(self, semantic_map: dict):
        relations = set((semantic_map.get("relations") or {}).keys())
        intents = semantic_map.get("intents") or {}
        self.handlers: dict[str, Handler] = {}
        for intent, (rels, fn) in self.registry.items():
            if intent not in intents:
                continue
            missing = [r for r in rels if r not in relations]
            if missing:
                raise ValueError(f"intent {intent}: relations {missing} are not declared in the semantic_map")
            self.handlers[intent] = fn

    def answer(self, parsed: Parsed, ctx: Context) -> Answer | None:
        fn = self.handlers.get(parsed.intent or "")
        if not fn:
            return None
        if "breakdown" in parsed.notes and ctx.generated_enabled:
            return None          # 「部門別」「上位」… are not covered by the fixed query; let path 2 try
        m = ctx.semantic_map
        as_of_req = parsed.as_of
        assumed = False
        if as_of_req is None:
            default = (m.get("time") or {}).get("default_as_of")
            if not default:
                return Answer("clarify", "いつ時点の数字ですか？「先月末時点」「1月末時点」「2月10日時点」のように指定してください。",
                              intent=parsed.intent, hints=[f"先月末時点の{_TOPIC.get(parsed.intent, '')}は？"])
            as_of_req, assumed = {"mode": default}, True
        try:
            as_of = resolve_as_of(as_of_req, ctx.base, ctx.tz)
        except ValueError:
            return Answer("clarify", f"基準日（{ctx.base.isoformat()}）より後の時点は照会できません。", intent=parsed.intent,
                          hints=STARTER_HINTS[:2])
        as_of["assumed"] = assumed
        with ctx.connect() as cx:
            a = fn(cx, parsed, as_of, ctx)
        a.as_of = as_of
        if assumed and a.path == "fixed":
            a.answer_ja = f"（時点の指定がないため{as_of['label_ja']}で回答）\n" + a.answer_ja
        return a


_TOPIC = {"unpaid_summary": "未払い出張精算", "attendance_unconfirmed": "勤怠未確定の案件", "claim_detail": "精算書の明細"}


def fixed_query(intent: str, relations: list[str]):
    def deco(fn: Handler) -> Handler:
        FixedQueryPath.registry[intent] = (relations, fn)
        return fn
    return deco


def _scope(parsed: Parsed) -> tuple[str, list]:
    if parsed.department:
        return " AND attributed_department_ja = %s", [parsed.department]
    return "", []


@fixed_query("unpaid_summary", ["sem.claim_balance_at", "sem.attendance_issues_at"])
def _unpaid_summary(cx, parsed: Parsed, as_of: dict, ctx: Context) -> Answer:
    where, params = _scope(parsed)
    rows = cx.execute(f"""SELECT claim_id, employee_id, employee_name_ja, attributed_department_ja, balance_yen, anomaly_code
                          FROM sem.claim_balance_at(%s, %s) WHERE TRUE{where} ORDER BY balance_yen DESC, claim_id""",
                      [as_of["cutoff"], ctx.base, *params]).fetchall()
    unpaid = [r for r in rows if r["balance_yen"] > 0 and r["anomaly_code"] is None]
    anomalies = sorted((r for r in rows if r["anomaly_code"]), key=lambda r: r["claim_id"])
    total = sum(r["balance_yen"] for r in unpaid)
    people = len({r["employee_id"] for r in unpaid})
    scope = f"{parsed.department}（帰属部門）の" if parsed.department else ""
    text = f"{as_of['label_ja']}で{scope}承認済み・未払いの出張精算は {yen(total)}（{len(unpaid)}件・{people}名）です。"
    if anomalies:
        labels = {"OVERPAID": "超過支払", "NO_LINES": "明細なし"}
        text += "\nデータ異常 " + "、".join(f"{r['claim_id']}（{labels.get(r['anomaly_code'], r['anomaly_code'])}）" for r in anomalies) + " は合計から除外し、財務の確認対象です。"
    data = {"total_balance_yen": total, "claim_count": len(unpaid), "employee_count": people,
            "claims": [{k: r[k] for k in ("claim_id", "employee_name_ja", "attributed_department_ja", "balance_yen")} for r in unpaid],
            "anomalies": [{"claim_id": r["claim_id"], "anomaly_code": r["anomaly_code"]} for r in anomalies]}
    hints = ["そのうち勤怠未確定は何名？"]
    if parsed.also_attendance:
        att = _attendance_data(cx, as_of, {r["claim_id"] for r in unpaid})
        data["attendance_unconfirmed"] = att
        text += "\n" + _attendance_text(att, prefix="そのうち")
        hints = [f"{att['issues'][0]['claim_id']} の明細を見せて"] if att["issues"] else []
    if unpaid:
        hints.append(f"{unpaid[0]['claim_id']} の明細を見せて")
    return Answer("fixed", text, intent="unpaid_summary", data=data, hints=list(dict.fromkeys(hints))[:2])


def _attendance_data(cx, as_of: dict, claim_ids: set[str] | None = None) -> dict:
    rows = cx.execute("""SELECT i.claim_id, i.employee_id, b.employee_name_ja, i.work_date, i.reason_code, i.reason_ja,
                                i.responsible_role, i.evidence_id, b.balance_yen
                         FROM sem.attendance_issues_at(%s) i JOIN sem.claim_balance_at(%s) b USING (claim_id)
                         WHERE i.claim_unpaid ORDER BY i.claim_id, i.work_date, i.reason_code""",
                      [as_of["cutoff"], as_of["cutoff"]]).fetchall()
    if claim_ids is not None:
        rows = [r for r in rows if r["claim_id"] in claim_ids]
    claims = {r["claim_id"]: r["balance_yen"] for r in rows}
    by_role: dict[str, int] = {}
    for r in rows:
        by_role[r["responsible_role"]] = by_role.get(r["responsible_role"], 0) + 1
    return {"claim_count": len(claims), "employee_count": len({r["employee_id"] for r in rows}),
            "balance_yen": sum(claims.values()), "issue_count": len(rows), "by_responsible_role": by_role,
            "issues": [{"claim_id": r["claim_id"], "employee_name_ja": r["employee_name_ja"], "work_date": r["work_date"].isoformat(),
                        "reason_code": r["reason_code"], "reason_ja": r["reason_ja"], "responsible_role": r["responsible_role"],
                        "evidence_id": r["evidence_id"]} for r in rows]}


def _attendance_text(att: dict, prefix: str = "") -> str:
    if not att["issues"]:
        return f"{prefix}勤怠未確定の案件はありません。"
    roles = "、".join(f"{k} {v}件" for k, v in sorted(att["by_responsible_role"].items(), key=lambda kv: (-kv[1], kv[0])))
    lines = "\n".join(f"・{i['claim_id']} {i['employee_name_ja']} {i['work_date']}：{i['reason_ja']} → {i['responsible_role']}"
                      for i in att["issues"])
    return (f"{prefix}勤怠未確定は {att['employee_count']}名・{att['claim_count']}件（対象残高 {yen(att['balance_yen'])}）、"
            f"確認事項 {att['issue_count']}件（{roles}）です。\n{lines}")


@fixed_query("attendance_unconfirmed", ["sem.claim_balance_at", "sem.attendance_issues_at"])
def _attendance_unconfirmed(cx, parsed: Parsed, as_of: dict, ctx: Context) -> Answer:
    ids = None
    if parsed.department:
        where, params = _scope(parsed)
        ids = {r["claim_id"] for r in cx.execute(f"SELECT claim_id FROM sem.claim_balance_at(%s) WHERE TRUE{where}",
                                                 [as_of["cutoff"], *params]).fetchall()}
    att = _attendance_data(cx, as_of, ids)
    text = f"{as_of['label_ja']}の未払い出張精算のうち、" + _attendance_text(att)
    hints = [f"{att['issues'][0]['claim_id']} の明細を見せて"] if att["issues"] else []
    hints.append("先月末時点の未払い出張精算はいくら？")
    return Answer("fixed", text, intent="attendance_unconfirmed", data=att, hints=hints[:2])


@fixed_query("claim_detail", ["sem.claim_evidence_at"])
def _claim_detail(cx, parsed: Parsed, as_of: dict, ctx: Context) -> Answer:
    if not parsed.claim_id:
        return Answer("clarify", "精算書番号（例：C1001）を指定してください。", intent="claim_detail", hints=["C1001 の明細を見せて"])
    rows = cx.execute("SELECT * FROM sem.claim_evidence_at(%s, %s)", [parsed.claim_id, as_of["cutoff"]]).fetchall()
    head = next((r for r in rows if r["kind"] == "claim"), None)
    if not head:
        return Answer("fixed", f"{as_of['label_ja']}で精算書 {parsed.claim_id} は見つかりません。", intent="claim_detail",
                      data={"claim_id": parsed.claim_id, "found": False}, hints=STARTER_HINTS[:2])
    fmt = lambda ts: ts.astimezone(ctx.tz).strftime("%Y-%m-%d %H:%M") if ts else ""  # noqa: E731
    lines = [r for r in rows if r["kind"] == "line"]
    appr = [r for r in rows if r["kind"] == "approval"]
    pays = [r for r in rows if r["kind"] == "payment"]
    approved = sum(r["amount_yen"] or 0 for r in lines)
    paid = sum(r["amount_yen"] for r in pays)
    text = (f"{parsed.claim_id}「{head['label_ja']}」（{head['detail']['employee_name_ja']}）{as_of['label_ja']}\n"
            f"明細 {len(lines)}件・承認額 {yen(approved)}："
            + "、".join(f"{r['label_ja']} {yen(r['amount_yen'] or 0)}" for r in lines)
            + "\n承認履歴：" + " → ".join(f"{r['label_ja']}（{fmt(r['occurred_at'])}）" for r in appr)
            + f"\n支払：{len(pays)}件・純額 {yen(paid)}" + ("".join(f"\n・{r['label_ja']} {yen(r['amount_yen'])}（{fmt(r['occurred_at'])}）" for r in pays)))
    data = {"claim_id": parsed.claim_id, "found": True, "title_ja": head["label_ja"], **head["detail"],
            "approved_total_yen": approved, "net_paid_yen": paid,
            "lines": [{"line_no": r["seq"], "category_ja": r["label_ja"], "approved_amount_yen": r["amount_yen"], **r["detail"]} for r in lines],
            "approvals": [{"seq": r["seq"], "action": r["label_ja"], "occurred_at": r["occurred_at"].isoformat(), **r["detail"]} for r in appr],
            "payments": [{"entry_type": r["label_ja"], "amount_yen": r["amount_yen"], "settled_at": r["occurred_at"].isoformat(), **r["detail"]} for r in pays]}
    return Answer("fixed", text, intent="claim_detail", data=data,
                  hints=["先月末時点の未払い出張精算はいくら？", "勤怠未確定は何名？"])


# ------------------------------------------------------------------ engine
def plugin_paths(semantic_map: dict) -> list[AnswerPath]:
    """Answer paths published by installed packages (entry-point group `lexigate.answer_paths`)."""
    out = []
    if os.environ.get("LEXIGATE_DISABLE_PLUGINS") == "1":
        return out
    for ep in entry_points(group="lexigate.answer_paths"):
        out.append(ep.load()(semantic_map))
    return out


def _default_paths(semantic_map: dict, llm) -> list[AnswerPath]:
    paths: list[AnswerPath] = [FixedQueryPath(semantic_map)]
    if llm == "auto":
        from lexigate.llm import provider_from_env
        llm = provider_from_env()
    if llm is not None:
        from lexigate.generated import GeneratedSQLPath, GenerationDisabled
        try:
            paths.append(GeneratedSQLPath(semantic_map, llm))
        except GenerationDisabled:
            pass
    return paths + plugin_paths(semantic_map)


class Engine:
    """Thread-safe. Keeps, per session, the id of the last answer, and per answer (bounded LRU) what is
    needed to explain it later. Nothing is persisted."""

    MAX_SESSIONS = 1000
    MAX_ANSWERS = 2000

    def __init__(self, semantic_map: dict, dsn: str, base: date, nlu: NLU | None = None,
                 paths: list[AnswerPath] | None = None, llm: Any = None):
        tz = ZoneInfo(semantic_map.get("timezone", "Asia/Tokyo"))
        self.ctx = Context(semantic_map, base, dsn, tz)
        self.nlu = nlu or RulesNLU(semantic_map)
        self.paths: list[AnswerPath] = paths if paths is not None else _default_paths(semantic_map, llm)
        self.ctx.generated_enabled = any(getattr(p, "name", "") == "generated" for p in self.paths)
        self._lock = threading.Lock()
        self._sessions: OrderedDict[str, str] = OrderedDict()
        self._answers: OrderedDict[str, dict] = OrderedDict()

    # -------------------------------------------------------------- state
    def _remember(self, a: Answer, session_id: str | None) -> None:
        with self._lock:
            self._answers[a.query_id] = {"path": a.path, "ai_generated": a.ai_generated, "rationale": a.rationale,
                                         "intent": a.intent, "as_of": a.as_of}
            while len(self._answers) > self.MAX_ANSWERS:
                self._answers.popitem(last=False)
            if session_id:
                self._sessions[session_id] = a.query_id
                self._sessions.move_to_end(session_id)
                while len(self._sessions) > self.MAX_SESSIONS:
                    self._sessions.popitem(last=False)

    def last_query_id(self, session_id: str | None) -> str | None:
        with self._lock:
            return self._sessions.get(session_id) if session_id else None

    def evidence(self, query_id: str | None) -> dict:
        """SQL and rationale of a previous path-2 answer (MCP get_evidence kind=sql_and_rationale)."""
        with self._lock:
            rec = self._answers.get(query_id or "")
        if not rec:
            return {"status": "not_found", "message": "回答が見つかりません（期限切れ、または別のセッション）。"}
        if not rec["ai_generated"] or not rec["rationale"]:
            msg = ("直前の回答は定型クエリ（セマンティック層の固定クエリ）による回答で、AI 生成の SQL はありません。"
                   if rec["path"] == "fixed" else "直前の回答には SQL がありません。")
            return {"status": "not_applicable", "path": rec["path"], "message": msg}
        return {"status": "ok", "query_id": query_id, **rec["rationale"]}

    def _confirm_answer(self, session_id: str | None) -> Answer:
        qid = self.last_query_id(session_id)
        ev = self.evidence(qid)
        if ev["status"] != "ok":
            text = ev["message"] if qid else "直前の回答が見つかりません。先に質問してください。"
            return Answer("evidence", text, intent="confirm_sql", data={"status": ev["status"], "query_id": qid},
                          hints=STARTER_HINTS[:2])
        lines = ["直前の回答（AI 作成）の SQL と根拠です。", "【SQL】", ev["sql"],
                 f"【時点】{ev['as_of']['label_ja']}（:cutoff = {ev['as_of']['cutoff']}、この時刻より前のデータ）"]
        for name, r in ev["relations"].items():
            lines.append(f"【参照したリレーション】{r.get('call', name)}（粒度：{r.get('grain', '—')}）")
        for name, mt in ev["metrics"].items():
            lines.append(f"【口径】{name}：{mt.get('definition_ja') or mt.get('definition', '')}"
                         + (f"（条件：{mt['filter']}）" if mt.get("filter") else ""))
        for k, t in ev["terms"].items():
            lines.append(f"【用語】{k} = {t.get('maps_to') if isinstance(t, dict) else t}")
        for k, r in ev["rules"].items():
            lines.append(f"【ルール {k}】{r}")
        if ev.get("basis_ja"):
            lines.append(f"【AI の説明】{ev['basis_ja']}")
        return Answer("evidence", "\n".join(lines), intent="confirm_sql",
                      data={"status": "ok", "query_id": qid, "sql": ev["sql"], "relations": ev["relations"],
                            "metrics": ev["metrics"], "terms": ev["terms"], "rules": ev["rules"],
                            "basis_ja": ev.get("basis_ja"), "model": ev.get("model")},
                      hints=STARTER_HINTS[:2])

    # -------------------------------------------------------------- ask
    def ask(self, question: str, session_id: str | None = None) -> Answer:
        m = self.ctx.semantic_map
        parsed = self.nlu.parse(question, self.ctx.base)
        if parsed.intent == "confirm_sql":
            a = self._confirm_answer(session_id)
        elif parsed.intent == "help":
            a = Answer("help", HELP_JA, intent="help", hints=STARTER_HINTS[:2])
        else:
            a = None
            for path in self.paths:
                a = path.answer(parsed, self.ctx)
                if a is not None:
                    break
            if a is None:
                a = Answer("clarify", CLARIFY_JA + "\n" + "\n".join(f"・{h}" for h in STARTER_HINTS), hints=STARTER_HINTS[:2])
        a.semantic_version = m.get("semantic_version")
        a.data_label = m.get("data_label_ja") or m.get("data_label")
        a.nlu = {"source": parsed.source, "intent": parsed.intent, "confidence": parsed.confidence,
                 **({"department": parsed.department} if parsed.department else {}),
                 **({"claim_id": parsed.claim_id} if parsed.claim_id else {})}
        a.query_id = uuid.uuid4().hex[:16]
        a.session_id = session_id
        if a.path != "evidence":                 # the follow-up keeps pointing at the answer it explains
            self._remember(a, session_id)
        return a

    def health(self) -> dict[str, Any]:
        with self.ctx.connect() as cx:
            cx.execute("SELECT 1").fetchone()
        return {"ok": True, "base_date": self.ctx.base.isoformat(),
                "semantic_version": self.ctx.semantic_map.get("semantic_version"),
                "generated_sql": self.ctx.generated_enabled}
