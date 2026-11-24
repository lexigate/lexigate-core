"""Independent pure-Python implementation of the month-end rules (design §3), used to produce expected.json.
It shares NO code with the SQL semantic layer; tests compare both, plus the hand-written reference SQL."""
from datetime import date, datetime, timedelta
from generator.calendar_jp import JST, BASE_DATE, jst_midnight, daterange
from generator.principals import PRINCIPALS, GRANTS

CUTOFFS = {  # label -> exclusive cutoff (JST midnight)
    "2026-01_month_end": date(2026, 2, 1), "as_of_2026-02-05": date(2026, 2, 6), "as_of_2026-02-10": date(2026, 2, 11),
    "as_of_2026-02-13": date(2026, 2, 14), "2026-02_month_end": date(2026, 3, 1), "as_of_2026-03-09": date(2026, 3, 10),
}
ROLE = {"NO_SHIFT": "人事", "NO_ATTENDANCE": "本人", "NOT_CLOSED": "人事", "CORRECTION_PENDING": "部門承認者"}

def ts(s):
    return None if s in (None, "") else datetime.fromisoformat(s)

def balances(T, cutoff_d):
    cut = jst_midnight(cutoff_d)
    last = {}
    for e in T["claim_approval_events"]:
        t = ts(e["occurred_at"])
        if e["is_valid"] and t < cut:
            k = (t, e["event_seq"])
            if e["claim_id"] not in last or k > last[e["claim_id"]][0]: last[e["claim_id"]] = (k, e["action"], t)
    trips = {t["trip_id"]: t for t in T["trip_requests"]}
    pay_by_id = {p["entry_id"]: p for p in T["payment_entries"]}
    out = []
    for c in T["expense_claims"]:
        cid = c["claim_id"]
        if c["business_type"] != "TRIP" or not c["trip_id"] or ts(c["submitted_at"]) >= cut: continue
        if cid not in last or last[cid][1] != "FINAL_APPROVE": continue
        tr = trips[c["trip_id"]]
        lines = [l for l in T["expense_lines"] if l["claim_id"] == cid and l["approved_amount_yen"] is not None]
        approved = sum(l["approved_amount_yen"] for l in lines)
        net = 0
        for p in T["payment_entries"]:
            if p["claim_id"] != cid or p["status"] != "SETTLED" or ts(p["settled_at"]) >= cut: continue
            if p["entry_type"] == "REVERSAL":
                o = pay_by_id.get(p["reverses_entry_id"])
                if not o or o["claim_id"] != cid or o["entry_type"] != "PAYMENT" or o["status"] != "SETTLED" or ts(o["settled_at"]) >= cut: continue
            net += p["amount_yen"]
        dept = next((a["department_id"] for a in T["employment_assignments"] if a["employee_id"] == c["employee_id"] and a["is_primary"]
                     and a["valid_from"] <= tr["start_date"] and (a["valid_to"] is None or tr["start_date"] < a["valid_to"])), None)
        bal = approved - net
        out.append({"claim_id": cid, "employee_id": c["employee_id"], "trip_start": tr["start_date"], "trip_end": tr["end_date"],
                    "attributed_department_id": dept, "approved_total_yen": approved, "net_paid_yen": net, "balance_yen": bal,
                    "anomaly_code": "NO_LINES" if not lines else ("OVERPAID" if bal < 0 else None)})
    return sorted(out, key=lambda r: r["claim_id"])

def issues(T, cutoff_d, claims):
    cut = jst_midnight(cutoff_d)
    shifts = {(s["employee_id"], s["work_date"]): s for s in T["shift_assignments"]}
    res = []
    for c in claims:
        for d in daterange(c["trip_start"], c["trip_end"]):
            if d >= cutoff_d: continue
            emp = c["employee_id"]; sa = shifts.get((emp, d))
            def latest(rows, key):
                best = {}
                for r in rows:
                    if r["employee_id"] == emp and r["work_date"] == d and ts(r["occurred_at"]) < cut:
                        k = (ts(r["occurred_at"]), r["event_seq"])
                        if r[key] not in best or k > best[r[key]][0]: best[r[key]] = (k, r)
                return [v[1] for v in best.values()]
            full_leave = any(r["status"] == "APPROVED" and r["unit"] == "FULL" for r in latest(T["leave_requests"], "leave_id"))
            corr = any(r["status"] == "SUBMITTED" for r in latest(T["attendance_corrections"], "correction_id"))
            att = [a for a in T["attendance_days"] if a["employee_id"] == emp and a["work_date"] == d and ts(a["effective_at"]) < cut]
            att = max(att, key=lambda a: (ts(a["effective_at"]), a["version"]))["status"] if att else None
            codes = []
            if full_leave: continue
            if sa is None: codes.append("NO_SHIFT")
            elif sa["day_type"] == "WORK":
                if att is None: codes.append("NO_ATTENDANCE")
                elif att == "OPEN": codes.append("NOT_CLOSED")
            if corr: codes.append("CORRECTION_PENDING")
            for code in codes:
                res.append({"claim_id": c["claim_id"], "employee_id": emp, "work_date": d, "reason_code": code, "responsible_role": ROLE[code]})
    return res

def current_dept(T, emp, today=BASE_DATE):
    return next((a["department_id"] for a in T["employment_assignments"] if a["employee_id"] == emp and a["is_primary"]
                 and a["valid_from"] <= today and (a["valid_to"] is None or today < a["valid_to"])), None)

def visible(T, principal, domain, emp):
    company = {e["employee_id"]: e["company_id"] for e in T["employees"]}
    parent = {d["department_id"]: d["parent_department_id"] for d in T["departments"]}
    def under(dep, root):
        while dep:
            if dep == root: return True
            dep = parent.get(dep)
        return False
    for p, dom, kind, val in GRANTS:
        if p != principal or dom != domain: continue
        if kind == "EMPLOYEE" and val == emp: return True
        if kind == "COMPANY" and company.get(emp) == val: return True
        if kind == "DEPARTMENT" and under(current_dept(T, emp), val): return True
    return False

def summarize(rows, iss):
    unpaid = [r for r in rows if r["balance_yen"] > 0 and r["anomaly_code"] is None]
    ids = {r["claim_id"] for r in unpaid}
    iss_u = [i for i in iss if i["claim_id"] in ids]
    flagged = {i["claim_id"] for i in iss_u}
    return {"total_balance_yen": sum(r["balance_yen"] for r in unpaid), "claim_count": len(unpaid),
            "employee_count": len({r["employee_id"] for r in unpaid}),
            "claim_ids": sorted(ids),
            "attendance_unconfirmed": {"claim_count": len(flagged), "balance_yen": sum(r["balance_yen"] for r in unpaid if r["claim_id"] in flagged),
                                       "claim_ids": sorted(flagged),
                                       "issues": sorted([(i["claim_id"], str(i["work_date"]), i["reason_code"], i["responsible_role"]) for i in iss_u])},
            "anomalies": sorted([(r["claim_id"], r["anomaly_code"]) for r in rows if r["anomaly_code"]])}

def expected_results(T):
    out = {"cutoffs": {}, "by_principal": {}}
    for label, cd in CUTOFFS.items():
        rows = balances(T, cd); iss = issues(T, cd, rows)
        out["cutoffs"][label] = {"cutoff_jst": f"{cd}T00:00:00+09:00", "summary": summarize(rows, iss),
                                 "claims": [{k: (str(v) if isinstance(v, date) else v) for k, v in r.items()} for r in rows]}
        for pid, *_ in PRINCIPALS:
            vis = [r for r in rows if visible(T, pid, "CLAIMS", r["employee_id"])]
            vis_iss = [i for i in iss if i["claim_id"] in {r["claim_id"] for r in vis}]
            out["by_principal"].setdefault(pid, {})[label] = summarize(vis, vis_iss)
    # hand-confirmed acceptance values (design §5); generator asserts the model agrees
    acc = {
        "case1_multi_line": {"cutoff": "2026-02_month_end", "claim_id": "C1001", "approved_total_yen": 10000, "net_paid_yen": 3000, "balance_yen": 7000},
        "case2_cutoff_reversal": {"C1002": {"2026-01_month_end": 20000, "as_of_2026-02-05": 0, "as_of_2026-02-10": 20000,
                                            "2026-02_month_end": 12000, "as_of_2026-03-09": 0}},
        "case3_revoke_reapprove": {"C1003": {"as_of_2026-02-05": 15000, "2026-02_month_end": None, "as_of_2026-03-09": 15000}},
    }
    by = {lab: {c["claim_id"]: c for c in v["claims"]} for lab, v in out["cutoffs"].items()}
    c1 = by["2026-02_month_end"]["C1001"]
    assert (c1["approved_total_yen"], c1["net_paid_yen"], c1["balance_yen"]) == (10000, 3000, 7000), c1
    for lab, bal in acc["case2_cutoff_reversal"]["C1002"].items(): assert by[lab]["C1002"]["balance_yen"] == bal, (lab, by[lab]["C1002"])
    for lab, bal in acc["case3_revoke_reapprove"]["C1003"].items():
        assert (by[lab].get("C1003") or {}).get("balance_yen") == bal, (lab, by[lab].get("C1003"))
    out["acceptance_hand_confirmed"] = acc
    return out
