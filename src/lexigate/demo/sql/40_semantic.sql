-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LexiGate contributors
--
-- Semantic surface (schema sem) for the quickstart demo. Every function takes the cutoff (exclusive upper
-- bound, timestamptz) so the same definition serves month-end and historical as-of questions.
-- This open-core version has NO row-level security: the quickstart answers company-wide on fictional data.
-- (Entra-based RLS / CLS is an enterprise plug-in and is not part of lexigate-core.)
-- The functions are SECURITY DEFINER with a fixed search_path, so the low-privilege role `lexigate_query`
-- (used for model-generated SQL, see 50_query_role.sql) can call them without any access to schema `demo`.
SET check_function_bodies = off;
DROP SCHEMA IF EXISTS sem CASCADE;
CREATE SCHEMA sem;

-- Approval state: last VALID approval event strictly before the cutoff (ties broken by event_seq).
CREATE FUNCTION sem.claim_status_at(p_cutoff timestamptz)
RETURNS TABLE(claim_id text, last_action text, last_event_at timestamptz, last_event_id text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, sem, demo AS $$
  SELECT DISTINCT ON (e.claim_id) e.claim_id, e.action, e.occurred_at, e.event_id
  FROM demo.claim_approval_events e
  WHERE e.is_valid AND e.occurred_at < p_cutoff
  ORDER BY e.claim_id, e.occurred_at DESC, e.event_seq DESC
$$;

-- Balance per claim: approved line total and net settled payments are aggregated SEPARATELY per claim and
-- then joined on claim_id (no fan-out). Scope: TRIP claims with a trip, submitted and FINAL_APPROVE'd before cutoff.
-- p_base (optional) is the server base date used for current_department_id; defaults to the day before the cutoff.
CREATE FUNCTION sem.claim_balance_at(p_cutoff timestamptz, p_base date DEFAULT NULL)
RETURNS TABLE(claim_id text, employee_id text, employee_number text, employee_name_ja text, trip_id text,
              trip_start date, trip_end date, attributed_department_id text, attributed_department_ja text,
              current_department_id text, final_approved_at timestamptz, line_count integer,
              approved_total_yen bigint, net_paid_yen bigint, balance_yen bigint, anomaly_code text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, sem, demo AS $$
  WITH st AS (
    SELECT s.claim_id, s.last_event_at FROM sem.claim_status_at(p_cutoff) s WHERE s.last_action = 'FINAL_APPROVE'
  ), c AS (
    SELECT c.claim_id, c.employee_id, c.trip_id, t.start_date, t.end_date, st.last_event_at
    FROM demo.expense_claims c
    JOIN st ON st.claim_id = c.claim_id
    JOIN demo.trip_requests t ON t.trip_id = c.trip_id AND t.employee_id = c.employee_id
    WHERE c.business_type = 'TRIP' AND c.submitted_at < p_cutoff
  ), l AS (
    SELECT x.claim_id, count(x.approved_amount_yen)::int AS line_count, COALESCE(sum(x.approved_amount_yen), 0)::bigint AS approved
    FROM demo.expense_lines x WHERE x.claim_id IN (SELECT claim_id FROM c) GROUP BY x.claim_id
  ), p AS (
    SELECT pe.claim_id, sum(pe.amount_yen)::bigint AS net
    FROM demo.payment_entries pe
    WHERE pe.claim_id IN (SELECT claim_id FROM c) AND pe.status = 'SETTLED' AND pe.settled_at < p_cutoff
      AND (pe.entry_type = 'PAYMENT' OR EXISTS (
            SELECT 1 FROM demo.payment_entries o
            WHERE o.entry_id = pe.reverses_entry_id AND o.claim_id = pe.claim_id AND o.entry_type = 'PAYMENT'
              AND o.status = 'SETTLED' AND o.settled_at < p_cutoff))
    GROUP BY pe.claim_id
  )
  SELECT c.claim_id, c.employee_id, e.employee_number, e.display_name_ja, c.trip_id, c.start_date, c.end_date,
         a.department_id, d.name_ja, cur.department_id, c.last_event_at,
         COALESCE(l.line_count, 0), COALESCE(l.approved, 0), COALESCE(p.net, 0),
         COALESCE(l.approved, 0) - COALESCE(p.net, 0),
         CASE WHEN COALESCE(l.line_count, 0) = 0 THEN 'NO_LINES'
              WHEN COALESCE(l.approved, 0) - COALESCE(p.net, 0) < 0 THEN 'OVERPAID' END
  FROM c
  JOIN demo.employees e ON e.employee_id = c.employee_id
  LEFT JOIN l ON l.claim_id = c.claim_id
  LEFT JOIN p ON p.claim_id = c.claim_id
  LEFT JOIN demo.employment_assignments a ON a.employee_id = c.employee_id AND a.is_primary
       AND c.start_date >= a.valid_from AND (a.valid_to IS NULL OR c.start_date < a.valid_to)   -- attribution by trip start date
  LEFT JOIN demo.departments d ON d.department_id = a.department_id
  LEFT JOIN LATERAL (
    SELECT ca.department_id FROM demo.employment_assignments ca
    WHERE ca.employee_id = c.employee_id AND ca.is_primary
      AND COALESCE(p_base, (p_cutoff AT TIME ZONE 'Asia/Tokyo')::date - 1) >= ca.valid_from
      AND (ca.valid_to IS NULL OR COALESCE(p_base, (p_cutoff AT TIME ZONE 'Asia/Tokyo')::date - 1) < ca.valid_to)
    LIMIT 1) cur ON true
$$;

-- Attendance status per trip work date (JST) of final-approved trip claims.
CREATE FUNCTION sem.attendance_issues_at(p_cutoff timestamptz)
RETURNS TABLE(claim_id text, employee_id text, work_date date, reason_code text, reason_ja text,
              responsible_role text, evidence_id text, claim_visible boolean, claim_unpaid boolean)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, sem, demo AS $$
  WITH b AS (
    SELECT * FROM sem.claim_balance_at(p_cutoff)
  ), d AS (
    SELECT b.claim_id, b.employee_id, g::date AS work_date, (b.balance_yen > 0 AND b.anomaly_code IS NULL) AS claim_unpaid
    FROM b CROSS JOIN LATERAL generate_series(b.trip_start, b.trip_end, interval '1 day') g
    WHERE g::date < (p_cutoff AT TIME ZONE 'Asia/Tokyo')::date          -- days not yet due are not "unconfirmed"
  ), x AS (
    SELECT d.*, sa.day_type, (sa.shift_assignment_id IS NULL) AS no_shift,
      EXISTS (SELECT 1 FROM (SELECT DISTINCT ON (lr.leave_id) lr.status, lr.unit FROM demo.leave_requests lr
                             WHERE lr.employee_id = d.employee_id AND lr.work_date = d.work_date AND lr.occurred_at < p_cutoff
                             ORDER BY lr.leave_id, lr.occurred_at DESC, lr.event_seq DESC) z
              WHERE z.status = 'APPROVED' AND z.unit = 'FULL') AS full_leave,
      (SELECT ad.status FROM demo.attendance_days ad
        WHERE ad.employee_id = d.employee_id AND ad.work_date = d.work_date AND ad.effective_at < p_cutoff
        ORDER BY ad.effective_at DESC, ad.version DESC LIMIT 1) AS att_status,
      EXISTS (SELECT 1 FROM (SELECT DISTINCT ON (ac.correction_id) ac.status FROM demo.attendance_corrections ac
                             WHERE ac.employee_id = d.employee_id AND ac.work_date = d.work_date AND ac.occurred_at < p_cutoff
                             ORDER BY ac.correction_id, ac.occurred_at DESC, ac.event_seq DESC) z
              WHERE z.status = 'SUBMITTED') AS corr_pending
    FROM d LEFT JOIN demo.shift_assignments sa ON sa.employee_id = d.employee_id AND sa.work_date = d.work_date
  ), r AS (
    SELECT x.claim_id, x.employee_id, x.work_date, x.claim_unpaid, v.code, v.label, v.role
    FROM x CROSS JOIN LATERAL (VALUES
        (x.no_shift AND NOT x.full_leave,                                                        'NO_SHIFT',           'シフト未登録（要確認）', '人事'),
        (NOT x.no_shift AND x.day_type = 'WORK' AND NOT x.full_leave AND x.att_status IS NULL,   'NO_ATTENDANCE',      '打刻・日次勤怠なし',     '本人'),
        (NOT x.no_shift AND x.day_type = 'WORK' AND NOT x.full_leave AND x.att_status = 'OPEN',  'NOT_CLOSED',         '日次勤怠が未締め',       '人事'),
        (NOT x.full_leave AND x.corr_pending,                                                    'CORRECTION_PENDING', '勤怠補正の承認待ち',     '部門承認者')
      ) v(hit, code, label, role)
    WHERE v.hit
  )
  SELECT r.claim_id, r.employee_id, r.work_date, r.code, r.label, r.role,
         'ATT-' || r.employee_id || '-' || to_char(r.work_date, 'YYYYMMDD'), true, r.claim_unpaid
  FROM r
$$;

-- Evidence for one claim as of the cutoff: lines, approval events and payment entries in one tagged row set.
CREATE FUNCTION sem.claim_evidence_at(p_claim_id text, p_cutoff timestamptz)
RETURNS TABLE(kind text, seq integer, occurred_at timestamptz, label_ja text, amount_yen bigint, detail jsonb)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, sem, demo AS $$
  SELECT 'claim', 0, c.submitted_at, c.title_ja, NULL::bigint,
         jsonb_build_object('employee_id', c.employee_id, 'employee_name_ja', e.display_name_ja, 'trip_id', c.trip_id,
                            'business_type', c.business_type, 'policy_version_id', c.policy_version_id)
  FROM demo.expense_claims c JOIN demo.employees e USING (employee_id)
  WHERE c.claim_id = p_claim_id AND c.submitted_at < p_cutoff
  UNION ALL
  SELECT 'line', l.line_no, NULL, l.category_ja, l.approved_amount_yen,
         jsonb_build_object('transaction_date', l.transaction_date, 'claimed_amount_yen', l.claimed_amount_yen)
  FROM demo.expense_lines l JOIN demo.expense_claims c USING (claim_id)
  WHERE l.claim_id = p_claim_id AND c.submitted_at < p_cutoff
  UNION ALL
  SELECT 'approval', a.event_seq, a.occurred_at, a.action, NULL,
         jsonb_build_object('actor_role', a.actor_role, 'stage', a.stage, 'comment_ja', a.comment_ja)
  FROM demo.claim_approval_events a
  WHERE a.claim_id = p_claim_id AND a.is_valid AND a.occurred_at < p_cutoff
  UNION ALL
  SELECT 'payment', row_number() OVER (ORDER BY p.settled_at, p.entry_id)::int, p.settled_at, p.entry_type, p.amount_yen,
         jsonb_build_object('entry_id', p.entry_id, 'reverses_entry_id', p.reverses_entry_id)
  FROM demo.payment_entries p
  WHERE p.claim_id = p_claim_id AND p.status = 'SETTLED' AND p.settled_at < p_cutoff
  ORDER BY 1, 2
$$;

-- Status-only view of one work date (no clock times).
CREATE FUNCTION sem.workday_status(p_employee text, p_date date, p_cutoff timestamptz)
RETURNS TABLE(employee_id text, work_date date, day_type text, shift_type_ja text, attendance_status text,
              attendance_version integer, full_day_leave boolean, half_day_leave text, correction_pending boolean)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, sem, demo AS $$
  SELECT p_employee, p_date, sa.day_type, st.name_ja,
         ad.status, ad.version,
         COALESCE(lv.unit = 'FULL' AND lv.status = 'APPROVED', false),
         CASE WHEN lv.status = 'APPROVED' AND lv.unit IN ('AM','PM') THEN lv.unit END,
         EXISTS (SELECT 1 FROM (SELECT DISTINCT ON (ac.correction_id) ac.status FROM demo.attendance_corrections ac
                                WHERE ac.employee_id = p_employee AND ac.work_date = p_date AND ac.occurred_at < p_cutoff
                                ORDER BY ac.correction_id, ac.occurred_at DESC, ac.event_seq DESC) z WHERE z.status = 'SUBMITTED')
  FROM (SELECT 1) one
  LEFT JOIN demo.shift_assignments sa ON sa.employee_id = p_employee AND sa.work_date = p_date
  LEFT JOIN demo.shift_types st ON st.shift_type_id = sa.shift_type_id
  LEFT JOIN LATERAL (SELECT a.status, a.version FROM demo.attendance_days a
                     WHERE a.employee_id = p_employee AND a.work_date = p_date AND a.effective_at < p_cutoff
                     ORDER BY a.effective_at DESC, a.version DESC LIMIT 1) ad ON true
  LEFT JOIN LATERAL (SELECT DISTINCT ON (l.leave_id) l.status, l.unit FROM demo.leave_requests l
                     WHERE l.employee_id = p_employee AND l.work_date = p_date AND l.occurred_at < p_cutoff
                     ORDER BY l.leave_id, l.occurred_at DESC, l.event_seq DESC LIMIT 1) lv ON true
$$;

-- Clock events with the work date they belong to (night shift -> the start work date via shift assignment).
CREATE FUNCTION sem.clock_workdays()
RETURNS TABLE(clock_event_id text, employee_id text, direction text, occurred_at timestamptz, occurred_jst timestamp, work_date date)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, sem, demo AS $$
  SELECT ce.clock_event_id, ce.employee_id, ce.direction, ce.occurred_at, ce.occurred_at AT TIME ZONE 'Asia/Tokyo',
         COALESCE(sa.work_date, (ce.occurred_at AT TIME ZONE 'Asia/Tokyo')::date)
  FROM demo.clock_events ce LEFT JOIN demo.shift_assignments sa ON sa.shift_assignment_id = ce.shift_assignment_id
$$;

COMMENT ON SCHEMA sem IS 'セマンティック層。締め時点（排他的上限）を引数に取る関数で指標を定義する（デモ：行レベル権限なし）';
COMMENT ON FUNCTION sem.claim_status_at(timestamptz) IS '精算書の承認状態（締め時点前の最後の有効な承認アクション）';
COMMENT ON FUNCTION sem.claim_balance_at(timestamptz, date) IS '承認済み出張精算の未払い残高（精算書単位）。balance_yen=承認明細合計－確定支払（有効な取消を控除）、anomaly_code=OVERPAID/NO_LINES/NULL';
COMMENT ON FUNCTION sem.attendance_issues_at(timestamptz) IS '勤怠未確定の判定（精算書×勤務日×理由）。NO_SHIFT/NO_ATTENDANCE/NOT_CLOSED/CORRECTION_PENDING と確認担当';
COMMENT ON FUNCTION sem.claim_evidence_at(text, timestamptz) IS '精算書1件の証跡（明細・承認履歴・支払）。締め時点前のもののみ';
COMMENT ON FUNCTION sem.workday_status(text, date, timestamptz) IS '勤務日の状態（打刻時刻を含まない状態のみ）。day_type、shift_type_ja、attendance_status（OPEN/CLOSED/NULL）、休暇、補正承認待ち';
COMMENT ON FUNCTION sem.clock_workdays() IS '打刻と帰属勤務日（夜勤の退勤打刻は始業日に帰属）';
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA sem FROM PUBLIC;
