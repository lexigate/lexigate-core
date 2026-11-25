-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LexiGate contributors
--
-- LexiGate quickstart: fictional month-end demo schema (16 business tables, schema `demo`).
-- Times: *_at are timestamptz stored in UTC; business dates (work_date, *_date) are Asia/Tokyo dates.
-- Money: bigint JPY (integer yen, never floating point). All people and companies are fictitious.
CREATE SCHEMA demo;
SET search_path = demo, public;

CREATE TABLE departments (
  department_id        text PRIMARY KEY,
  department_code      text NOT NULL UNIQUE,
  name_ja              text NOT NULL,
  parent_department_id text REFERENCES departments(department_id),
  company_id           text NOT NULL,
  source_ref           text NOT NULL,
  CHECK (parent_department_id IS DISTINCT FROM department_id)
);

CREATE TABLE employees (
  employee_id      text PRIMARY KEY,
  employee_number  text NOT NULL UNIQUE,
  display_name_ja  text NOT NULL,
  company_id       text NOT NULL,
  employed_from    date NOT NULL,
  employed_to      date,
  is_demo_data     boolean NOT NULL DEFAULT true,
  source_ref       text NOT NULL,
  CHECK (employed_to IS NULL OR employed_to > employed_from)
);

CREATE TABLE employment_assignments (
  assignment_id  text PRIMARY KEY,
  employee_id    text NOT NULL REFERENCES employees(employee_id),
  department_id  text NOT NULL REFERENCES departments(department_id),
  is_primary     boolean NOT NULL DEFAULT true,
  valid_from     date NOT NULL,
  valid_to       date,                                    -- exclusive; NULL = open-ended  [valid_from, valid_to)
  source_ref     text NOT NULL,
  CHECK (valid_to IS NULL OR valid_to > valid_from)
);
CREATE INDEX ix_assign_dept ON employment_assignments (department_id, valid_from);

CREATE TABLE close_periods (
  period_id            text PRIMARY KEY,                  -- 'YYYY-MM'
  month_start          date NOT NULL UNIQUE,
  month_end_exclusive  date NOT NULL,
  snapshot_cutoff_at   timestamptz NOT NULL,              -- = month_end_exclusive 00:00 JST, exclusive upper bound
  planned_close_at     timestamptz NOT NULL,
  status               text NOT NULL CHECK (status IN ('OPEN','CLOSING','CLOSED')),
  source_ref           text NOT NULL,
  CHECK (month_end_exclusive > month_start)
);

CREATE TABLE shift_types (
  shift_type_id          text PRIMARY KEY,
  name_ja                text NOT NULL,
  planned_start          time NOT NULL,
  planned_end            time NOT NULL,
  crosses_midnight       boolean NOT NULL,
  planned_break_minutes  integer NOT NULL CHECK (planned_break_minutes >= 0),
  source_ref             text NOT NULL,
  CHECK (crosses_midnight = (planned_end <= planned_start))
);

CREATE TABLE shift_assignments (
  shift_assignment_id  text PRIMARY KEY,
  employee_id          text NOT NULL REFERENCES employees(employee_id),
  work_date            date NOT NULL,                     -- night shift belongs to the work date it starts on
  day_type             text NOT NULL CHECK (day_type IN ('WORK','REST','HOLIDAY')),
  shift_type_id        text REFERENCES shift_types(shift_type_id),
  source_ref           text NOT NULL,
  UNIQUE (employee_id, work_date),
  CHECK ((day_type = 'WORK') = (shift_type_id IS NOT NULL))
);

CREATE TABLE clock_events (
  clock_event_id       text PRIMARY KEY,
  employee_id          text NOT NULL REFERENCES employees(employee_id),
  shift_assignment_id  text REFERENCES shift_assignments(shift_assignment_id),
  direction            text NOT NULL CHECK (direction IN ('IN','OUT')),
  occurred_at          timestamptz NOT NULL,
  source_event_id      text NOT NULL UNIQUE,
  source_ref           text NOT NULL
);
CREATE INDEX ix_clock_emp_time ON clock_events (employee_id, occurred_at);
CREATE INDEX ix_clock_shift ON clock_events (shift_assignment_id);

CREATE TABLE attendance_days (
  attendance_day_id  text PRIMARY KEY,
  employee_id        text NOT NULL REFERENCES employees(employee_id),
  work_date          date NOT NULL,
  version            integer NOT NULL CHECK (version >= 1),
  effective_at       timestamptz NOT NULL,
  worked_minutes     integer NOT NULL CHECK (worked_minutes >= 0),
  status             text NOT NULL CHECK (status IN ('OPEN','CLOSED')),
  source_ref         text NOT NULL,
  UNIQUE (employee_id, work_date, version)
);
CREATE INDEX ix_attday_emp_date_eff ON attendance_days (employee_id, work_date, effective_at);

CREATE TABLE attendance_corrections (
  event_id                    text PRIMARY KEY,
  correction_id               text NOT NULL,
  employee_id                 text NOT NULL REFERENCES employees(employee_id),
  work_date                   date NOT NULL,
  event_seq                   integer NOT NULL CHECK (event_seq >= 1),
  status                      text NOT NULL CHECK (status IN ('SUBMITTED','APPROVED','REJECTED','WITHDRAWN')),
  occurred_at                 timestamptz NOT NULL,
  resulting_attendance_day_id text REFERENCES attendance_days(attendance_day_id),
  reason_ja                   text,
  source_ref                  text NOT NULL,
  UNIQUE (correction_id, event_seq),
  CHECK (status <> 'APPROVED' OR resulting_attendance_day_id IS NOT NULL)
);
CREATE INDEX ix_corr_emp_date ON attendance_corrections (employee_id, work_date, occurred_at);

CREATE TABLE leave_requests (
  event_id     text PRIMARY KEY,
  leave_id     text NOT NULL,
  employee_id  text NOT NULL REFERENCES employees(employee_id),
  work_date    date NOT NULL,
  unit         text NOT NULL CHECK (unit IN ('FULL','AM','PM')),
  event_seq    integer NOT NULL CHECK (event_seq >= 1),
  status       text NOT NULL CHECK (status IN ('SUBMITTED','APPROVED','REJECTED','CANCELLED')),
  occurred_at  timestamptz NOT NULL,
  source_ref   text NOT NULL,
  UNIQUE (leave_id, event_seq)
);
CREATE INDEX ix_leave_emp_date ON leave_requests (employee_id, work_date, occurred_at);

CREATE TABLE policy_versions (
  policy_version_id  text PRIMARY KEY,
  policy_code        text NOT NULL,
  version            text NOT NULL,
  valid_from         date NOT NULL,
  valid_to           date,
  description_ja     text NOT NULL,
  params             jsonb NOT NULL DEFAULT '{}'::jsonb,
  source_ref         text NOT NULL,
  UNIQUE (policy_code, version),
  CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE TABLE trip_requests (
  trip_id            text PRIMARY KEY,
  employee_id        text NOT NULL REFERENCES employees(employee_id),
  start_date         date NOT NULL,
  end_date           date NOT NULL,
  destination_ja     text NOT NULL,
  purpose_ja         text NOT NULL,
  submitted_at       timestamptz NOT NULL,
  final_approved_at  timestamptz,
  policy_version_id  text NOT NULL REFERENCES policy_versions(policy_version_id),
  source_ref         text NOT NULL,
  UNIQUE (trip_id, employee_id),
  CHECK (end_date >= start_date)
);

CREATE TABLE expense_claims (
  claim_id           text PRIMARY KEY,
  employee_id        text NOT NULL REFERENCES employees(employee_id),
  policy_version_id  text NOT NULL REFERENCES policy_versions(policy_version_id),
  trip_id            text,
  business_type      text NOT NULL CHECK (business_type IN ('TRIP','GENERAL')),
  title_ja           text NOT NULL,
  currency           text NOT NULL DEFAULT 'JPY' CHECK (currency = 'JPY'),
  submitted_at       timestamptz NOT NULL,
  source_ref         text NOT NULL,
  -- the trip must belong to the claimant
  FOREIGN KEY (trip_id, employee_id) REFERENCES trip_requests(trip_id, employee_id)
);
CREATE INDEX ix_claim_emp ON expense_claims (employee_id);

CREATE TABLE expense_lines (
  expense_line_id      text PRIMARY KEY,
  claim_id             text NOT NULL REFERENCES expense_claims(claim_id),
  line_no              integer NOT NULL CHECK (line_no >= 1),
  transaction_date     date NOT NULL,
  category_ja          text NOT NULL,
  claimed_amount_yen   bigint NOT NULL CHECK (claimed_amount_yen >= 0),
  approved_amount_yen  bigint CHECK (approved_amount_yen >= 0),
  source_ref           text NOT NULL,
  UNIQUE (claim_id, line_no)
);

CREATE TABLE claim_approval_events (
  event_id     text PRIMARY KEY,
  claim_id     text NOT NULL REFERENCES expense_claims(claim_id),
  event_seq    integer NOT NULL CHECK (event_seq >= 1),
  occurred_at  timestamptz NOT NULL,
  action       text NOT NULL CHECK (action IN ('SUBMIT','RETURN','RESUBMIT','APPROVE_STAGE','FINAL_APPROVE','REVOKE')),
  actor_role   text NOT NULL,
  stage        integer NOT NULL DEFAULT 0,
  is_valid     boolean NOT NULL DEFAULT true,               -- erroneous events are voided, never deleted
  comment_ja   text,
  source_ref   text NOT NULL,
  UNIQUE (claim_id, event_seq)
);
CREATE INDEX ix_appr_claim_time_seq ON claim_approval_events (claim_id, occurred_at, event_seq);

CREATE TABLE payment_entries (
  entry_id           text PRIMARY KEY,
  claim_id           text NOT NULL REFERENCES expense_claims(claim_id),
  entry_type         text NOT NULL CHECK (entry_type IN ('PAYMENT','REVERSAL')),
  status             text NOT NULL CHECK (status IN ('SCHEDULED','SETTLED','FAILED','CANCELLED')),
  settled_at         timestamptz,
  amount_yen         bigint NOT NULL,                       -- signed: payment > 0, reversal < 0
  reverses_entry_id  text REFERENCES payment_entries(entry_id),
  source_event_id    text NOT NULL UNIQUE,
  source_ref         text NOT NULL,
  CHECK ((entry_type = 'PAYMENT' AND amount_yen > 0 AND reverses_entry_id IS NULL)
      OR (entry_type = 'REVERSAL' AND amount_yen < 0 AND reverses_entry_id IS NOT NULL)),
  CHECK (status <> 'SETTLED' OR settled_at IS NOT NULL)
);
CREATE INDEX ix_pay_claim_settled ON payment_entries (claim_id, settled_at);

