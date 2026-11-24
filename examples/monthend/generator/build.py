#!/usr/bin/env python3
"""Deterministic demo-data generator for the LexiGate month-end demo (デモデータ).

* Employees / departments / company are SELECTED from the CC0 HR sample (schema sample_raw_100k, active employees,
  lowest ids per department) and re-keyed to virtual IDs; the source id is kept in source_ref + source_map.csv.
  Names are replaced by fixed fictitious Japanese names.
* Cross-domain records (shifts, clocks, attendance versions, corrections, leave, trips, claims, approvals, payments)
  are synthetic: fixed acceptance scenarios (design §5) + background claims from random.Random(SEED).
* Nothing at/after the snapshot cutoff (COVERAGE_END) is emitted.
Outputs data/demo/<DATA_SET>/: one CSV per table, source_map.csv, expected.json (from the independent Python
reference model + hand-confirmed acceptance values), manifest.json (row counts, sha256, versions, coverage).
"""
import csv, hashlib, json, os, random, sys
from datetime import date, datetime, timedelta
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
from generator.calendar_jp import (JST, COVERAGE_START, COVERAGE_END, COVERAGE_START_DATE, BASE_DATE, jst, jst_midnight,
                                   iso, daterange, day_type)
from generator import reference_model as RM

GENERATOR_VERSION = "lexigate-gen-1.0.0"
SEED = 20260310
DATA_SET = "v1"
RULE_VERSION = "close-rules-v1"
SEMANTIC_VERSION = "sem-2026.11.1"
COMPANY = ("C001", "株式会社デモ商事", "hr-sample-v2/100k:companies#1")
DEPTS = [  # virtual id, code, name, sample dept code (None = synthetic root), how many employees
    ("D000", "HQ", "全社", None, 0),
    ("D100", "SAL", "営業部", "SAL", 8),
    ("D200", "ENG", "開発部", "ENG", 8),
    ("D300", "HRS", "人事部", "HRS", 3),
    ("D400", "FIN", "経理部", "FIN", 3),
]
NAMES = ["佐藤 一郎", "鈴木 花子", "高橋 健太", "田中 美咲", "伊藤 翔", "渡辺 由美", "山本 大輔", "中村 さくら",
         "小林 直樹", "加藤 彩", "吉田 拓海", "山田 結衣", "佐々木 亮", "山口 真央", "松本 悠真", "井上 恵",
         "木村 蓮", "林 千尋", "清水 誠", "山崎 葵", "森 智也", "池田 遥"]
TABLE_ORDER = ["departments", "employees", "employment_assignments", "close_periods", "shift_types", "shift_assignments",
               "clock_events", "attendance_days", "attendance_corrections", "leave_requests", "policy_versions",
               "trip_requests", "expense_claims", "expense_lines", "claim_approval_events", "payment_entries"]
COLUMNS = {
    "departments": ["department_id", "department_code", "name_ja", "parent_department_id", "company_id", "source_ref"],
    "employees": ["employee_id", "employee_number", "display_name_ja", "company_id", "employed_from", "employed_to", "is_demo_data", "source_ref"],
    "employment_assignments": ["assignment_id", "employee_id", "department_id", "is_primary", "valid_from", "valid_to", "source_ref"],
    "close_periods": ["period_id", "month_start", "month_end_exclusive", "snapshot_cutoff_at", "planned_close_at", "status", "source_ref"],
    "shift_types": ["shift_type_id", "name_ja", "planned_start", "planned_end", "crosses_midnight", "planned_break_minutes", "source_ref"],
    "shift_assignments": ["shift_assignment_id", "employee_id", "work_date", "day_type", "shift_type_id", "source_ref"],
    "clock_events": ["clock_event_id", "employee_id", "shift_assignment_id", "direction", "occurred_at", "source_event_id", "source_ref"],
    "attendance_days": ["attendance_day_id", "employee_id", "work_date", "version", "effective_at", "worked_minutes", "status", "source_ref"],
    "attendance_corrections": ["event_id", "correction_id", "employee_id", "work_date", "event_seq", "status", "occurred_at", "resulting_attendance_day_id", "reason_ja", "source_ref"],
    "leave_requests": ["event_id", "leave_id", "employee_id", "work_date", "unit", "event_seq", "status", "occurred_at", "source_ref"],
    "policy_versions": ["policy_version_id", "policy_code", "version", "valid_from", "valid_to", "description_ja", "params", "source_ref"],
    "trip_requests": ["trip_id", "employee_id", "start_date", "end_date", "destination_ja", "purpose_ja", "submitted_at", "final_approved_at", "policy_version_id", "source_ref"],
    "expense_claims": ["claim_id", "employee_id", "policy_version_id", "trip_id", "business_type", "title_ja", "currency", "submitted_at", "source_ref"],
    "expense_lines": ["expense_line_id", "claim_id", "line_no", "transaction_date", "category_ja", "claimed_amount_yen", "approved_amount_yen", "source_ref"],
    "claim_approval_events": ["event_id", "claim_id", "event_seq", "occurred_at", "action", "actor_role", "stage", "is_valid", "comment_ja", "source_ref"],
    "payment_entries": ["entry_id", "claim_id", "entry_type", "status", "settled_at", "amount_yen", "reverses_entry_id", "source_event_id", "source_ref"],
}


def load_sample():
    import psycopg
    from scripts.pgenv import admin_dsn
    with psycopg.connect(admin_dsn()) as cx:
        depts = {code: (i, name) for i, code, name in cx.execute("select id, code, name from sample_raw_100k.departments")}
        emps = {}
        for vid, code, _n, scode, n in DEPTS:
            if not scode: continue
            emps[vid] = cx.execute("select id, hire_date from sample_raw_100k.employees where department_id=%s and status_id=1 "
                                   "and hire_date < '2025-10-01' order by id limit %s", (depts[scode][0], n)).fetchall()
        return depts, emps


class Gen:
    def __init__(self, depts, emps):
        self.T = {t: [] for t in TABLE_ORDER}
        self.rng = random.Random(SEED)
        self.source_map = []
        self.scenarios = {}
        self.n_clock = 0
        self.depts, self.emps = depts, emps
        self.dept_of = {}          # employee -> list of (dept, from, to)
        self.shift_of = {}         # (emp, date) -> shift_assignment row

    def add(self, table, **row):
        self.T[table].append(row); return row

    # ------------------------------------------------------------------ master data
    def master(self):
        cid, cname, csrc = COMPANY
        self.source_map.append({"virtual_id": cid, "kind": "company", "source": csrc, "note": cname})
        for vid, code, name, scode, _n in DEPTS:
            src = f"hr-sample-v2/100k:departments#{self.depts[scode][0]}({scode})" if scode else "synthetic:org-root"
            self.add("departments", department_id=vid, department_code=code, name_ja=name,
                     parent_department_id=None if vid == "D000" else "D000", company_id=cid, source_ref=src)
            self.source_map.append({"virtual_id": vid, "kind": "department", "source": src, "note": name})
        k = 0
        for vid, *_ in DEPTS[1:]:
            for sid, hire in self.emps[vid]:
                k += 1; eid = f"E{k:04d}"
                src = f"hr-sample-v2/100k:employees#{sid}"
                self.add("employees", employee_id=eid, employee_number=f"DM-{1000 + k}", display_name_ja=NAMES[k - 1],
                         company_id=cid, employed_from=hire, employed_to=None, is_demo_data=True, source_ref=src)
                self.source_map.append({"virtual_id": eid, "kind": "employee", "source": src, "note": "name replaced (fictitious)"})
                if eid == "E0004":   # transfer 営業部 -> 開発部 on 2026-02-16
                    self.assign(eid, "D100", hire, date(2026, 2, 16), "synthetic:S5-transfer")
                    self.assign(eid, "D200", date(2026, 2, 16), None, "synthetic:S5-transfer")
                else:
                    self.assign(eid, vid, hire, None, src)
        self.employees = [e["employee_id"] for e in self.T["employees"]]
        for pid, ms, me, status, close in [("2026-01", date(2026, 1, 1), date(2026, 2, 1), "CLOSED", jst(2026, 2, 4, 18)),
                                           ("2026-02", date(2026, 2, 1), date(2026, 3, 1), "CLOSING", jst(2026, 3, 4, 18)),
                                           ("2026-03", date(2026, 3, 1), date(2026, 4, 1), "OPEN", jst(2026, 4, 3, 18))]:
            self.add("close_periods", period_id=pid, month_start=ms, month_end_exclusive=me, snapshot_cutoff_at=iso(jst_midnight(me)),
                     planned_close_at=iso(close), status=status, source_ref="synthetic:close-calendar")
        self.add("shift_types", shift_type_id="DAY", name_ja="日勤", planned_start="09:00", planned_end="18:00",
                 crosses_midnight=False, planned_break_minutes=60, source_ref="synthetic:freee-like-shift")
        self.add("shift_types", shift_type_id="NIGHT", name_ja="夜勤", planned_start="22:00", planned_end="06:00",
                 crosses_midnight=True, planned_break_minutes=60, source_ref="synthetic:freee-like-shift")
        self.add("policy_versions", policy_version_id="TRAVEL-2025.04", policy_code="TRAVEL_EXPENSE", version="2025.04",
                 valid_from=date(2025, 4, 1), valid_to=date(2026, 2, 1), description_ja="出張旅費規程（2025年4月版）：日当2,000円、宿泊上限12,000円",
                 params=json.dumps({"per_diem_yen": 2000, "hotel_cap_yen": 12000}, ensure_ascii=False), source_ref="synthetic:policy")
        self.add("policy_versions", policy_version_id="TRAVEL-2026.02", policy_code="TRAVEL_EXPENSE", version="2026.02",
                 valid_from=date(2026, 2, 1), valid_to=None, description_ja="出張旅費規程（2026年2月改定）：日当3,000円、宿泊上限15,000円",
                 params=json.dumps({"per_diem_yen": 3000, "hotel_cap_yen": 15000}, ensure_ascii=False), source_ref="synthetic:policy")
        self.add("policy_versions", policy_version_id="CLOSE-RULES-V1", policy_code="MONTH_CLOSE", version=RULE_VERSION,
                 valid_from=date(2026, 1, 1), valid_to=None,
                 description_ja="月結判定ルール v1：先月末時点＝基準日のJST月初（排他的上限）。承認済み＝締め時点前の最後の有効承認イベントが最終承認。"
                                "未払い残高＝承認明細合計－締め時点前の確定支払（有効な取消を控除）。勤怠未確定＝日次勤怠なし・未締め・補正承認待ち。",
                 params=json.dumps({"cutoff": "first_day_of_base_month_jst_exclusive", "timezone": "Asia/Tokyo", "currency": "JPY"}),
                 source_ref="synthetic:policy")

    def assign(self, eid, dept, f, t, src):
        n = len(self.T["employment_assignments"]) + 1
        self.add("employment_assignments", assignment_id=f"A{n:04d}", employee_id=eid, department_id=dept, is_primary=True,
                 valid_from=f, valid_to=t, source_ref=src)

    # ------------------------------------------------------------------ attendance
    def attendance(self):
        night_worker = "E0009"
        skip_shift = {("E0010", date(2026, 2, 18))}                     # S6: missing shift assignment
        extra_work = {("E0009", date(2026, 1, 31)): "NIGHT"}            # S4: Saturday night shift
        overrides = {
            ("E0010", date(2026, 2, 16)): "full_leave",
            ("E0010", date(2026, 2, 17)): "am_leave_no_attendance",
            ("E0010", date(2026, 2, 18)): "no_shift_but_worked",
            ("E0010", date(2026, 2, 19)): "correction_pending",
            ("E0005", date(2026, 2, 24)): "open_day",
            ("E0011", date(2026, 2, 12)): "correction_approved",
        }
        last_date = (COVERAGE_END - timedelta(days=1)).date()
        for eid in self.employees:
            for d in daterange(COVERAGE_START_DATE, last_date):
                if (eid, d) in skip_shift:
                    sa = None
                else:
                    dt = day_type(d); st = None
                    if (eid, d) in extra_work: dt, st = "WORK", extra_work[(eid, d)]
                    elif dt == "WORK": st = "NIGHT" if eid == night_worker else "DAY"
                    sa = self.add("shift_assignments", shift_assignment_id=f"SA-{eid}-{d:%Y%m%d}", employee_id=eid, work_date=d,
                                  day_type=dt, shift_type_id=st,
                                  source_ref="synthetic:S6-attendance-exceptions" if (eid, d) in overrides else
                                             ("synthetic:S4-night-shift" if eid == night_worker else "synthetic:base-calendar"))
                    self.shift_of[(eid, d)] = sa
                    if dt != "WORK": continue
                ov = overrides.get((eid, d))
                st = sa["shift_type_id"] if sa else "DAY"
                if ov in ("full_leave", "am_leave_no_attendance"):
                    unit = "FULL" if ov == "full_leave" else "AM"
                    lid = f"LV-{eid}-{d:%Y%m%d}"
                    self.add("leave_requests", event_id=f"{lid}-1", leave_id=lid, employee_id=eid, work_date=d, unit=unit, event_seq=1,
                             status="SUBMITTED", occurred_at=iso(jst(2026, 2, 10, 10)), source_ref="synthetic:S6-attendance-exceptions")
                    self.add("leave_requests", event_id=f"{lid}-2", leave_id=lid, employee_id=eid, work_date=d, unit=unit, event_seq=2,
                             status="APPROVED", occurred_at=iso(jst(2026, 2, 11, 10)), source_ref="synthetic:S6-attendance-exceptions")
                    continue
                # regular clocks
                if st == "NIGHT":
                    t_in = datetime(d.year, d.month, d.day, 21, 50 + self.rng.randint(0, 9), tzinfo=JST)
                    t_out = datetime(d.year, d.month, d.day, 6, self.rng.randint(0, 15), tzinfo=JST) + timedelta(days=1)
                    close_at = datetime(d.year, d.month, d.day, 12, 0, tzinfo=JST) + timedelta(days=1)
                else:
                    t_in = datetime(d.year, d.month, d.day, 8, 45 + self.rng.randint(0, 14), tzinfo=JST)
                    t_out = datetime(d.year, d.month, d.day, 18, self.rng.randint(0, 59), tzinfo=JST) + timedelta(minutes=60 * self.rng.randint(0, 1))
                    close_at = datetime(d.year, d.month, d.day, 9, 0, tzinfo=JST) + timedelta(days=1)
                src = "synthetic:S6-attendance-exceptions" if ov else "synthetic:base-attendance"
                sa_id = sa["shift_assignment_id"] if sa else None
                self.clock(eid, sa_id, "IN", t_in, src)
                if ov in ("open_day", "correction_approved"):          # clock-out missing -> day stays OPEN
                    open_at = datetime(d.year, d.month, d.day, 23, 0, tzinfo=JST)
                    self.attday(eid, d, 1, open_at, 0, "OPEN", src)
                    if ov == "open_day":                              # HR closes it after the Feb close
                        self.attday(eid, d, 2, jst(2026, 3, 5, 10), 480, "CLOSED", src)
                    else:
                        cid = f"CR-{eid}-{d:%Y%m%d}"
                        self.add("attendance_corrections", event_id=f"{cid}-1", correction_id=cid, employee_id=eid, work_date=d, event_seq=1,
                                 status="SUBMITTED", occurred_at=iso(jst(2026, 2, 13, 9, 30)), resulting_attendance_day_id=None,
                                 reason_ja="退勤打刻漏れの補正", source_ref=src)
                        v2 = self.attday(eid, d, 2, jst(2026, 2, 14, 10), 480, "CLOSED", src)
                        self.add("attendance_corrections", event_id=f"{cid}-2", correction_id=cid, employee_id=eid, work_date=d, event_seq=2,
                                 status="APPROVED", occurred_at=iso(jst(2026, 2, 14, 10)), resulting_attendance_day_id=v2["attendance_day_id"],
                                 reason_ja="退勤打刻漏れの補正", source_ref=src)
                    continue
                self.clock(eid, sa_id, "OUT", t_out, src)
                br = 60
                self.attday(eid, d, 1, close_at, int((t_out - t_in).total_seconds() // 60) - br, "CLOSED", src)
                if ov == "correction_pending":
                    cid = f"CR-{eid}-{d:%Y%m%d}"
                    self.add("attendance_corrections", event_id=f"{cid}-1", correction_id=cid, employee_id=eid, work_date=d, event_seq=1,
                             status="SUBMITTED", occurred_at=iso(jst(2026, 2, 24, 10)), resulting_attendance_day_id=None,
                             reason_ja="退勤時刻の修正（客先対応で延長）", source_ref=src)

    def clock(self, eid, sa_id, direction, t, src):
        if t >= COVERAGE_END: return
        self.n_clock += 1
        self.add("clock_events", clock_event_id=f"CE-{self.n_clock:06d}", employee_id=eid, shift_assignment_id=sa_id, direction=direction,
                 occurred_at=iso(t), source_event_id=f"TERM-{eid}-{t.astimezone(JST):%Y%m%d%H%M}-{direction}", source_ref=src)

    def attday(self, eid, d, ver, eff, minutes, status, src):
        if eff >= COVERAGE_END: return None
        return self.add("attendance_days", attendance_day_id=f"AD-{eid}-{d:%Y%m%d}-v{ver}", employee_id=eid, work_date=d, version=ver,
                        effective_at=iso(eff), worked_minutes=max(minutes, 0), status=status, source_ref=src)

    # ------------------------------------------------------------------ claims
    def claim(self, cid, eid, trip, lines, events, payments=(), business_type="TRIP", src="synthetic", title=None, scenario=None):
        if events[0][0] >= COVERAGE_END: return False
        pv = "TRAVEL-2026.02" if (trip and trip[0] >= date(2026, 2, 1)) else "TRAVEL-2025.04"
        trip_id = None
        if trip:
            start, end, dest, purpose = trip
            trip_id = "T" + cid[1:]
            self.add("trip_requests", trip_id=trip_id, employee_id=eid, start_date=start, end_date=end, destination_ja=dest, purpose_ja=purpose,
                     submitted_at=iso(jst_midnight(start) - timedelta(days=7) + timedelta(hours=10)),
                     final_approved_at=iso(jst_midnight(start) - timedelta(days=5) + timedelta(hours=15)), policy_version_id=pv, source_ref=src)
        self.add("expense_claims", claim_id=cid, employee_id=eid, policy_version_id=pv, trip_id=trip_id, business_type=business_type,
                 title_ja=title or (f"{trip[2]}出張精算" if trip else "立替経費精算"), currency="JPY", submitted_at=iso(events[0][0]), source_ref=src)
        for i, (d, cat, claimed, approved) in enumerate(lines, 1):
            self.add("expense_lines", expense_line_id=f"{cid}-L{i}", claim_id=cid, line_no=i, transaction_date=d, category_ja=cat,
                     claimed_amount_yen=claimed, approved_amount_yen=approved, source_ref=src)
        seq = 0
        for t, action, role, stage, comment in events:
            if t >= COVERAGE_END: continue
            seq += 1
            self.add("claim_approval_events", event_id=f"{cid}-E{seq}", claim_id=cid, event_seq=seq, occurred_at=iso(t), action=action,
                     actor_role=role, stage=stage, is_valid=True, comment_ja=comment, source_ref=src)
        for eid_, etype, status, t, amount, rev in payments:
            if t is not None and t >= COVERAGE_END: continue
            self.add("payment_entries", entry_id=eid_, claim_id=cid, entry_type=etype, status=status, settled_at=iso(t) if status == "SETTLED" else None,
                     amount_yen=amount, reverses_entry_id=rev, source_event_id=f"BANK-{eid_}", source_ref=src)
        if scenario: self.scenarios.setdefault(scenario, []).append(cid)
        return True

    def scenarios_fixed(self):
        A = "申請者"; M = "部門承認者"; K = "経理"; F = "財務"
        # S1 multi-line / multi-approval / partial payment  (design §5 #1)
        self.claim("C1001", "E0001", (date(2026, 2, 2), date(2026, 2, 3), "大阪", "顧客訪問"),
                   [(date(2026, 2, 2), "交通費", 6000, 6000), (date(2026, 2, 2), "宿泊費", 4500, 4000)],
                   [(jst(2026, 2, 5, 10), "SUBMIT", A, 0, None), (jst(2026, 2, 6, 11), "APPROVE_STAGE", M, 1, None),
                    (jst(2026, 2, 9, 15), "APPROVE_STAGE", K, 2, "宿泊費は上限超過分を減額"), (jst(2026, 2, 10, 16), "FINAL_APPROVE", F, 3, None)],
                   [("P1001-1", "PAYMENT", "SETTLED", jst(2026, 2, 20, 11), 3000, None)],
                   src="synthetic:S1-multi-line-multi-approval", scenario="S1")
        # S2 time point & reversal (design §5 #2)
        self.claim("C1002", "E0002", (date(2026, 1, 19), date(2026, 1, 20), "名古屋", "展示会"),
                   [(date(2026, 1, 19), "交通費", 12000, 12000), (date(2026, 1, 19), "宿泊費", 8000, 8000)],
                   [(jst(2026, 1, 21, 10), "SUBMIT", A, 0, None), (jst(2026, 1, 22, 11), "APPROVE_STAGE", M, 1, None),
                    (jst(2026, 1, 26, 16), "FINAL_APPROVE", F, 2, None)],
                   [("P1002-1", "PAYMENT", "SETTLED", jst(2026, 2, 5, 10), 20000, None),
                    ("P1002-R1", "REVERSAL", "SETTLED", jst(2026, 2, 10, 10), -20000, "P1002-1"),
                    ("P1002-2", "PAYMENT", "SETTLED", jst(2026, 2, 26, 10), 8000, None),
                    ("P1002-3", "PAYMENT", "FAILED", None, 12000, None),
                    ("P1002-4", "PAYMENT", "SETTLED", jst(2026, 3, 5, 10), 12000, None)],
                   src="synthetic:S2-cutoff-and-reversal", scenario="S2")
        # S3 return / resubmit / final / revoke / re-approve (design §5 #3)
        self.claim("C1003", "E0003", (date(2026, 1, 26), date(2026, 1, 27), "福岡", "代理店研修"),
                   [(date(2026, 1, 26), "交通費", 9000, 9000), (date(2026, 1, 26), "宿泊費", 6000, 6000)],
                   [(jst(2026, 1, 28, 9), "SUBMIT", A, 0, None), (jst(2026, 1, 29, 10), "RETURN", M, 1, "領収書の添付不足"),
                    (jst(2026, 1, 30, 9), "RESUBMIT", A, 0, None), (jst(2026, 2, 2, 11), "APPROVE_STAGE", M, 1, None),
                    (jst(2026, 2, 3, 14), "FINAL_APPROVE", F, 2, None), (jst(2026, 2, 10, 11), "REVOKE", F, 2, "二重計上の疑いで承認取消"),
                    (jst(2026, 3, 2, 9), "RESUBMIT", A, 0, "重複なしを確認のうえ再提出"), (jst(2026, 3, 3, 11), "APPROVE_STAGE", M, 1, None),
                    (jst(2026, 3, 4, 15), "FINAL_APPROVE", F, 2, None)],
                   src="synthetic:S3-return-resubmit-revoke", scenario="S3")
        self.claim("C1004", "E0003", (date(2026, 2, 12), date(2026, 2, 13), "仙台", "現地調査"),
                   [(date(2026, 2, 12), "交通費", 5000, 5000)],
                   [(jst(2026, 2, 16, 10), "SUBMIT", A, 0, None), (jst(2026, 2, 17, 11), "APPROVE_STAGE", M, 1, None)],
                   src="synthetic:S3-stage-approval-only", scenario="S3")
        # S4 night shift crossing month end (design §5 #4)
        self.claim("C1005", "E0009", (date(2026, 1, 31), date(2026, 2, 1), "横浜データセンター", "夜間メンテナンス立会い"),
                   [(date(2026, 1, 31), "交通費", 3000, 3000), (date(2026, 1, 31), "日当", 4000, 4000)],
                   [(jst(2026, 2, 2, 10), "SUBMIT", A, 0, None), (jst(2026, 2, 3, 11), "APPROVE_STAGE", M, 1, None),
                    (jst(2026, 2, 4, 16), "FINAL_APPROVE", F, 2, None)],
                   src="synthetic:S4-night-shift", scenario="S4")
        # S5 transfer (design §5 #5): trip before the 2026-02-16 transfer -> attributed to 営業部
        self.claim("C1006", "E0004", (date(2026, 2, 9), date(2026, 2, 10), "広島", "既存顧客の引継ぎ"),
                   [(date(2026, 2, 9), "交通費", 5000, 5000), (date(2026, 2, 9), "宿泊費", 4000, 4000)],
                   [(jst(2026, 2, 18, 10), "SUBMIT", A, 0, None), (jst(2026, 2, 19, 11), "APPROVE_STAGE", M, 1, None),
                    (jst(2026, 2, 20, 16), "FINAL_APPROVE", F, 2, None)],
                   src="synthetic:S5-transfer", scenario="S5")
        # S6 attendance exceptions (design §5 #6)
        self.claim("C1007", "E0010", (date(2026, 2, 16), date(2026, 2, 19), "札幌", "導入支援"),
                   [(date(2026, 2, 16), "交通費", 18000, 18000), (date(2026, 2, 16), "宿泊費", 12000, 12000)],
                   [(jst(2026, 2, 20, 10), "SUBMIT", A, 0, None), (jst(2026, 2, 24, 11), "APPROVE_STAGE", M, 1, None),
                    (jst(2026, 2, 25, 16), "FINAL_APPROVE", F, 2, None)],
                   src="synthetic:S6-attendance-exceptions", scenario="S6")
        # S7 day not closed (人事)
        self.claim("C1008", "E0005", (date(2026, 2, 24), date(2026, 2, 25), "京都", "顧客提案"),
                   [(date(2026, 2, 24), "交通費", 7000, 7000), (date(2026, 2, 24), "宿泊費", 4000, 4000)],
                   [(jst(2026, 2, 26, 10), "SUBMIT", A, 0, None), (jst(2026, 2, 26, 15), "APPROVE_STAGE", M, 1, None),
                    (jst(2026, 2, 27, 16), "FINAL_APPROVE", F, 2, None)],
                   src="synthetic:S7-day-not-closed", scenario="S7")
        # S8 correction approved (history): Feb 12 OPEN -> correction -> CLOSED v2 on Feb 14
        self.claim("C1009", "E0011", (date(2026, 2, 12), date(2026, 2, 13), "金沢", "設備点検"),
                   [(date(2026, 2, 12), "交通費", 8000, 8000)],
                   [(jst(2026, 2, 13, 17), "SUBMIT", A, 0, None), (jst(2026, 2, 13, 18), "APPROVE_STAGE", M, 1, None),
                    (jst(2026, 2, 13, 19), "FINAL_APPROVE", F, 2, None)],
                   [("P1009-1", "PAYMENT", "SETTLED", jst(2026, 3, 6, 11), 8000, None)],
                   src="synthetic:S8-correction-approved", scenario="S8")
        # data anomalies (財務)
        self.claim("C1011", "E0012", (date(2026, 2, 5), date(2026, 2, 5), "静岡", "工場監査"),
                   [(date(2026, 2, 5), "交通費", 5000, 5000)],
                   [(jst(2026, 2, 9, 10), "SUBMIT", A, 0, None), (jst(2026, 2, 10, 11), "APPROVE_STAGE", M, 1, None),
                    (jst(2026, 2, 12, 16), "FINAL_APPROVE", F, 2, None)],
                   [("P1011-1", "PAYMENT", "SETTLED", jst(2026, 2, 20, 11), 6000, None)],
                   src="synthetic:A1-overpaid", scenario="A1")
        self.claim("C1012", "E0006", (date(2026, 2, 3), date(2026, 2, 4), "神戸", "商談"), [],
                   [(jst(2026, 2, 16, 10), "SUBMIT", A, 0, None), (jst(2026, 2, 17, 11), "APPROVE_STAGE", M, 1, None),
                    (jst(2026, 2, 18, 16), "FINAL_APPROVE", F, 2, None)],
                   src="synthetic:A2-no-lines", scenario="A2")
        # exclusions
        self.claim("C1013", "E0007", None, [(date(2026, 2, 10), "消耗品費", 4000, 4000)],
                   [(jst(2026, 2, 12, 10), "SUBMIT", A, 0, None), (jst(2026, 2, 13, 16), "FINAL_APPROVE", F, 1, None)],
                   business_type="GENERAL", src="synthetic:X1-non-trip", scenario="X1")
        self.claim("C1014", "E0008", (date(2026, 2, 25), date(2026, 2, 26), "新潟", "販売店訪問"),
                   [(date(2026, 2, 25), "交通費", 10000, 10000)],
                   [(jst(2026, 2, 27, 10), "SUBMIT", A, 0, None), (jst(2026, 2, 27, 15), "APPROVE_STAGE", M, 1, None),
                    (jst(2026, 3, 2, 16), "FINAL_APPROVE", F, 2, None)],
                   src="synthetic:X2-approved-after-cutoff", scenario="X2")
        self.claim("C1015", "E0013", (date(2026, 2, 2), date(2026, 2, 2), "千葉", "保守対応"),
                   [(date(2026, 2, 2), "交通費", 2400, 2400)],
                   [(jst(2026, 2, 3, 10), "SUBMIT", A, 0, None), (jst(2026, 2, 4, 16), "FINAL_APPROVE", F, 1, None)],
                   [("P1015-1", "PAYMENT", "SETTLED", jst(2026, 2, 13, 11), 2400, None)],
                   src="synthetic:X3-fully-paid", scenario="X3")

    def background(self):
        pool = ["E0006", "E0007", "E0008", "E0012", "E0013", "E0014", "E0015", "E0016", "E0017", "E0018", "E0020", "E0021"]
        dests = ["大阪", "名古屋", "福岡", "仙台", "広島", "札幌", "高松", "岡山", "熊本", "長野"]
        A = "申請者"; M = "部門承認者"; F = "財務"
        r = self.rng; n = 0; used = set()
        while n < 16:
            eid = r.choice(pool)
            start = date(2026, 1, 6) + timedelta(days=r.randint(0, 51))
            if day_type(start) != "WORK": continue
            dur = r.randint(1, 3); end = start + timedelta(days=dur - 1)
            if any((eid, d) in used for d in daterange(start, end)): continue
            if eid == "E0012" and start <= date(2026, 2, 5) <= end: continue
            for d in daterange(start, end): used.add((eid, d))
            n += 1; cid = f"C{2000 + n}"
            dest = r.choice(dests)
            lines = [(start, "交通費", r.randint(800, 3000) * 10, None)]
            if dur > 1: lines.append((start, "宿泊費", r.randint(60, 150) * 100 * (dur - 1), None))
            lines.append((start, "日当", (3000 if start >= date(2026, 2, 1) else 2000) * dur, None))
            lines = [(d, c, a, a - (500 if r.random() < 0.2 and c == "交通費" else 0)) for d, c, a, _ in lines]
            sub = jst_midnight(end) + timedelta(days=r.randint(1, 4), hours=10)
            ev = [(sub, "SUBMIT", A, 0, None), (sub + timedelta(days=1, hours=4), "APPROVE_STAGE", M, 1, None)]
            pays = []
            if r.random() > 0.15:
                fin = sub + timedelta(days=1 + r.randint(1, 3), hours=6)
                ev.append((fin, "FINAL_APPROVE", F, 2, None))
                total = sum(l[3] for l in lines); u = r.random()
                if u < 0.45:
                    pays.append((f"P{cid[1:]}-1", "PAYMENT", "SETTLED", fin + timedelta(days=r.randint(3, 12)), total, None))
                elif u < 0.65:
                    pays.append((f"P{cid[1:]}-1", "PAYMENT", "SETTLED", fin + timedelta(days=r.randint(3, 12)), (total // 2) // 1000 * 1000, None))
            self.claim(cid, eid, (start, end, dest, "営業・技術支援"), lines, ev, pays, src="synthetic:BG-random", scenario="BG")


def write(gen):
    out = os.path.join(BASE, "data", "demo", DATA_SET); os.makedirs(out, exist_ok=True)
    hashes, counts = {}, {}
    def dump(name, cols, rows):
        p = os.path.join(out, f"{name}.csv")
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n"); w.writerow(cols)
            for r in rows:
                w.writerow(["" if r.get(c) is None else ("true" if r.get(c) is True else "false" if r.get(c) is False else r.get(c)) for c in cols])
        hashes[f"{name}.csv"] = hashlib.sha256(open(p, "rb").read()).hexdigest()
        return len(rows)
    for t in TABLE_ORDER: counts[t] = dump(t, COLUMNS[t], gen.T[t])
    dump("source_map", ["virtual_id", "kind", "source", "note"], gen.source_map)
    expected = RM.expected_results(gen.T)
    expected["scenarios"] = gen.scenarios
    p = os.path.join(out, "expected.json"); json.dump(expected, open(p, "w"), ensure_ascii=False, indent=2, default=str)
    hashes["expected.json"] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    content_hash = hashlib.sha256("".join(hashes[k] for k in sorted(hashes)).encode()).hexdigest()[:10]
    manifest = {
        "label": "デモデータ（架空）— 実在の人物・企業とは関係ありません",
        "data_version": f"demo-{BASE_DATE:%Y%m%d}-{DATA_SET}+{content_hash}",
        "generator_version": GENERATOR_VERSION, "seed": SEED, "rule_version": RULE_VERSION, "semantic_version": SEMANTIC_VERSION,
        "base_date": str(BASE_DATE), "coverage_start": iso(COVERAGE_START), "coverage_end": iso(COVERAGE_END),
        "coverage_jst": {"from": str(COVERAGE_START_DATE), "to_inclusive": str(BASE_DATE - timedelta(days=1))},
        "sources": {"hr_sample": "https://databasesample.com/datasets/hr-sample-database (CC0-1.0, v2, tier 100k: employees/departments/companies selected)",
                    "synthetic": "generator/build.py (fixed seed); schema concepts calibrated on freee public specs"},
        "row_counts": counts, "file_hashes": hashes,
    }
    json.dump(manifest, open(os.path.join(out, "manifest.json"), "w"), ensure_ascii=False, indent=2)
    return manifest


def main():
    depts, emps = load_sample()
    g = Gen(depts, emps); g.master(); g.attendance(); g.scenarios_fixed(); g.background()
    m = write(g)
    print(json.dumps({"data_version": m["data_version"], "row_counts": m["row_counts"]}, ensure_ascii=False))

if __name__ == "__main__":
    main()
