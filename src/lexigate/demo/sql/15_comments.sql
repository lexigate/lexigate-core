-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 LexiGate contributors
--
-- 業務テーブル定義書（日本語コメント）。lexigate scan はこのコメントを説明・用語候補として読み取る。
-- 共通：*_at は UTC 保存の時刻（timestamptz）、*_date / work_date は Asia/Tokyo の業務日付、金額は円単位の整数。

-- 1 departments
COMMENT ON TABLE demo.departments IS '部門マスタ。組織ノード1件につき1行。親部門を参照して階層を表す（デモデータ）';
COMMENT ON COLUMN demo.departments.department_id IS '部門ID（内部の固定ID。例：D100）';
COMMENT ON COLUMN demo.departments.department_code IS '部門コード（一意。例：SAL=営業、ENG=開発、HRS=人事、FIN=経理、HQ=全社）';
COMMENT ON COLUMN demo.departments.name_ja IS '部門名（日本語）';
COMMENT ON COLUMN demo.departments.parent_department_id IS '親部門ID（最上位はNULL）';
COMMENT ON COLUMN demo.departments.company_id IS '会社ID（例：C001=株式会社デモ商事）';
COMMENT ON COLUMN demo.departments.source_ref IS 'データ出典（元サンプルのID、または synthetic:<シナリオ名>）';

-- 2 employees
COMMENT ON TABLE demo.employees IS '従業員マスタ。1人につき1行。在籍期間を保持する（氏名は架空のデモデータ）';
COMMENT ON COLUMN demo.employees.employee_id IS '従業員ID（内部の固定ID。例：E0001）';
COMMENT ON COLUMN demo.employees.employee_number IS '社員番号（一意。例：DM-1001）';
COMMENT ON COLUMN demo.employees.display_name_ja IS '氏名（表示用・架空）';
COMMENT ON COLUMN demo.employees.company_id IS '所属会社ID';
COMMENT ON COLUMN demo.employees.employed_from IS '入社日（在籍開始日）';
COMMENT ON COLUMN demo.employees.employed_to IS '退職日（在籍終了日・この日を含まない。在籍中はNULL）';
COMMENT ON COLUMN demo.employees.is_demo_data IS 'デモデータ区分（true=デモデータ（架空））';
COMMENT ON COLUMN demo.employees.source_ref IS 'データ出典（元サンプルの従業員ID）';

-- 3 employment_assignments
COMMENT ON TABLE demo.employment_assignments IS '所属履歴。1人の1所属期間につき1行。主務の期間は重複不可（期間は開始日を含み終了日を含まない）';
COMMENT ON COLUMN demo.employment_assignments.assignment_id IS '所属履歴ID';
COMMENT ON COLUMN demo.employment_assignments.employee_id IS '従業員ID';
COMMENT ON COLUMN demo.employment_assignments.department_id IS '所属部門ID';
COMMENT ON COLUMN demo.employment_assignments.is_primary IS '主務区分（true=主務、false=兼務）';
COMMENT ON COLUMN demo.employment_assignments.valid_from IS '所属開始日（この日を含む）';
COMMENT ON COLUMN demo.employment_assignments.valid_to IS '所属終了日（この日を含まない。継続中はNULL）';
COMMENT ON COLUMN demo.employment_assignments.source_ref IS 'データ出典';

-- 4 close_periods
COMMENT ON TABLE demo.close_periods IS '決算期間（月次締め）。表示する会計月1件につき1行';
COMMENT ON COLUMN demo.close_periods.period_id IS '期間ID（YYYY-MM）';
COMMENT ON COLUMN demo.close_periods.month_start IS '月初日（この日を含む）';
COMMENT ON COLUMN demo.close_periods.month_end_exclusive IS '翌月初日（月末の排他的上限）';
COMMENT ON COLUMN demo.close_periods.snapshot_cutoff_at IS '締め時点（翌月初0:00 JST。この時刻より前の事象を集計対象とする）';
COMMENT ON COLUMN demo.close_periods.planned_close_at IS '締め予定日時';
COMMENT ON COLUMN demo.close_periods.status IS '締め状態（OPEN=未締め、CLOSING=締め作業中、CLOSED=締め済み）';
COMMENT ON COLUMN demo.close_periods.source_ref IS 'データ出典';

-- 5 shift_types
COMMENT ON TABLE demo.shift_types IS 'シフト区分マスタ（勤務パターンのテンプレート）';
COMMENT ON COLUMN demo.shift_types.shift_type_id IS 'シフト区分ID（DAY=日勤、NIGHT=夜勤）';
COMMENT ON COLUMN demo.shift_types.name_ja IS 'シフト名称';
COMMENT ON COLUMN demo.shift_types.planned_start IS '所定始業時刻（JST）';
COMMENT ON COLUMN demo.shift_types.planned_end IS '所定終業時刻（JST）';
COMMENT ON COLUMN demo.shift_types.crosses_midnight IS '日跨ぎ区分（true=終業が翌日。夜勤は始業日の勤務日に帰属）';
COMMENT ON COLUMN demo.shift_types.planned_break_minutes IS '所定休憩時間（分）';
COMMENT ON COLUMN demo.shift_types.source_ref IS 'データ出典';

-- 6 shift_assignments
COMMENT ON TABLE demo.shift_assignments IS 'シフト割当（勤務予定）。従業員×勤務日につき1行';
COMMENT ON COLUMN demo.shift_assignments.shift_assignment_id IS 'シフト割当ID';
COMMENT ON COLUMN demo.shift_assignments.employee_id IS '従業員ID';
COMMENT ON COLUMN demo.shift_assignments.work_date IS '勤務日（JST。夜勤は始業日）';
COMMENT ON COLUMN demo.shift_assignments.day_type IS '日区分（WORK=出勤日、REST=休日、HOLIDAY=祝日・会社休日）';
COMMENT ON COLUMN demo.shift_assignments.shift_type_id IS 'シフト区分ID（出勤日のみ設定。休日・祝日はNULL）';
COMMENT ON COLUMN demo.shift_assignments.source_ref IS 'データ出典';

-- 7 clock_events
COMMENT ON TABLE demo.clock_events IS '打刻記録。打刻1回につき1行';
COMMENT ON COLUMN demo.clock_events.clock_event_id IS '打刻ID';
COMMENT ON COLUMN demo.clock_events.employee_id IS '従業員ID';
COMMENT ON COLUMN demo.clock_events.shift_assignment_id IS '対応するシフト割当ID（勤務日の帰属先。未割当はNULL）';
COMMENT ON COLUMN demo.clock_events.direction IS '打刻区分（IN=出勤、OUT=退勤）';
COMMENT ON COLUMN demo.clock_events.occurred_at IS '打刻日時（UTC保存）';
COMMENT ON COLUMN demo.clock_events.source_event_id IS '打刻端末の元イベントID（一意）';
COMMENT ON COLUMN demo.clock_events.source_ref IS 'データ出典';

-- 8 attendance_days
COMMENT ON TABLE demo.attendance_days IS '勤怠日次（版管理）。従業員×勤務日×版番号につき1行。補正承認で新しい版が作られる';
COMMENT ON COLUMN demo.attendance_days.attendance_day_id IS '勤怠日次ID';
COMMENT ON COLUMN demo.attendance_days.employee_id IS '従業員ID';
COMMENT ON COLUMN demo.attendance_days.work_date IS '勤務日（JST）';
COMMENT ON COLUMN demo.attendance_days.version IS '版番号（1から。大きいほど新しい）';
COMMENT ON COLUMN demo.attendance_days.effective_at IS 'この版の有効開始日時（UTC保存）';
COMMENT ON COLUMN demo.attendance_days.worked_minutes IS '実労働時間（分。休憩控除後）';
COMMENT ON COLUMN demo.attendance_days.status IS '勤怠日次の締め状態（OPEN=未締め、CLOSED=締め済み）';
COMMENT ON COLUMN demo.attendance_days.source_ref IS 'データ出典';

-- 9 attendance_corrections
COMMENT ON TABLE demo.attendance_corrections IS '勤怠補正申請の状態イベント。補正1件の状態変化1回につき1行。承認時に新しい勤怠日次の版を生成';
COMMENT ON COLUMN demo.attendance_corrections.event_id IS '補正イベントID';
COMMENT ON COLUMN demo.attendance_corrections.correction_id IS '補正申請ID（同一申請のイベントで共通）';
COMMENT ON COLUMN demo.attendance_corrections.employee_id IS '従業員ID';
COMMENT ON COLUMN demo.attendance_corrections.work_date IS '補正対象の勤務日（JST）';
COMMENT ON COLUMN demo.attendance_corrections.event_seq IS 'イベント順序（同一申請内の連番）';
COMMENT ON COLUMN demo.attendance_corrections.status IS '補正状態（SUBMITTED=申請中・承認待ち、APPROVED=承認、REJECTED=却下、WITHDRAWN=取下げ）';
COMMENT ON COLUMN demo.attendance_corrections.occurred_at IS 'イベント発生日時（UTC保存）';
COMMENT ON COLUMN demo.attendance_corrections.resulting_attendance_day_id IS '承認により生成された勤怠日次ID（承認時のみ）';
COMMENT ON COLUMN demo.attendance_corrections.reason_ja IS '補正理由';
COMMENT ON COLUMN demo.attendance_corrections.source_ref IS 'データ出典';

-- 10 leave_requests
COMMENT ON TABLE demo.leave_requests IS '休暇申請の状態イベント。休暇1件（1日分）の状態変化1回につき1行';
COMMENT ON COLUMN demo.leave_requests.event_id IS '休暇イベントID';
COMMENT ON COLUMN demo.leave_requests.leave_id IS '休暇申請ID（同一申請のイベントで共通）';
COMMENT ON COLUMN demo.leave_requests.employee_id IS '従業員ID';
COMMENT ON COLUMN demo.leave_requests.work_date IS '休暇取得日（JST）';
COMMENT ON COLUMN demo.leave_requests.unit IS '取得単位（FULL=全日、AM=午前半休、PM=午後半休）';
COMMENT ON COLUMN demo.leave_requests.event_seq IS 'イベント順序（同一申請内の連番）';
COMMENT ON COLUMN demo.leave_requests.status IS '休暇状態（SUBMITTED=申請中、APPROVED=承認、REJECTED=却下、CANCELLED=取消）';
COMMENT ON COLUMN demo.leave_requests.occurred_at IS 'イベント発生日時（UTC保存）';
COMMENT ON COLUMN demo.leave_requests.source_ref IS 'データ出典';

-- 11 policy_versions
COMMENT ON TABLE demo.policy_versions IS '規程バージョン。規程1件の1版につき1行（出張旅費規程、月次締め判定ルール等）';
COMMENT ON COLUMN demo.policy_versions.policy_version_id IS '規程バージョンID（例：TRAVEL-2026.02）';
COMMENT ON COLUMN demo.policy_versions.policy_code IS '規程コード（TRAVEL_EXPENSE=出張旅費規程、MONTH_CLOSE=月次締め判定ルール）';
COMMENT ON COLUMN demo.policy_versions.version IS '版';
COMMENT ON COLUMN demo.policy_versions.valid_from IS '適用開始日（この日を含む）';
COMMENT ON COLUMN demo.policy_versions.valid_to IS '適用終了日（この日を含まない。適用中はNULL）';
COMMENT ON COLUMN demo.policy_versions.description_ja IS '規程の説明（日本語）';
COMMENT ON COLUMN demo.policy_versions.params IS '規程パラメータ（JSON。例：日当額、宿泊上限額）';
COMMENT ON COLUMN demo.policy_versions.source_ref IS 'データ出典';

-- 12 trip_requests
COMMENT ON TABLE demo.trip_requests IS '出張申請。出張1件につき1行';
COMMENT ON COLUMN demo.trip_requests.trip_id IS '出張申請ID';
COMMENT ON COLUMN demo.trip_requests.employee_id IS '出張者の従業員ID';
COMMENT ON COLUMN demo.trip_requests.start_date IS '出張開始日（JST。精算の部門帰属日として使用）';
COMMENT ON COLUMN demo.trip_requests.end_date IS '出張終了日（JST・この日を含む）';
COMMENT ON COLUMN demo.trip_requests.destination_ja IS '出張先';
COMMENT ON COLUMN demo.trip_requests.purpose_ja IS '出張目的';
COMMENT ON COLUMN demo.trip_requests.submitted_at IS '申請日時（UTC保存）';
COMMENT ON COLUMN demo.trip_requests.final_approved_at IS '出張申請の最終承認日時（UTC保存）';
COMMENT ON COLUMN demo.trip_requests.policy_version_id IS '適用した出張旅費規程のバージョンID';
COMMENT ON COLUMN demo.trip_requests.source_ref IS 'データ出典';

-- 13 expense_claims
COMMENT ON TABLE demo.expense_claims IS '経費精算書（ヘッダ）。精算書1件につき1行。出張精算は申請者本人の出張申請に紐づく';
COMMENT ON COLUMN demo.expense_claims.claim_id IS '精算書番号（例：C1001）';
COMMENT ON COLUMN demo.expense_claims.employee_id IS '申請者の従業員ID';
COMMENT ON COLUMN demo.expense_claims.policy_version_id IS '適用した規程のバージョンID';
COMMENT ON COLUMN demo.expense_claims.trip_id IS '紐づく出張申請ID（出張精算のみ。その他はNULL）';
COMMENT ON COLUMN demo.expense_claims.business_type IS '精算種別（TRIP=出張精算、GENERAL=一般経費精算）';
COMMENT ON COLUMN demo.expense_claims.title_ja IS '件名';
COMMENT ON COLUMN demo.expense_claims.currency IS '通貨（JPY=日本円のみ）';
COMMENT ON COLUMN demo.expense_claims.submitted_at IS '初回申請日時（UTC保存）';
COMMENT ON COLUMN demo.expense_claims.source_ref IS 'データ出典';

-- 14 expense_lines
COMMENT ON TABLE demo.expense_lines IS '経費精算明細。明細1行につき1行。申請後は変更不可（金額変更は別の精算書で行う）';
COMMENT ON COLUMN demo.expense_lines.expense_line_id IS '明細ID';
COMMENT ON COLUMN demo.expense_lines.claim_id IS '精算書番号';
COMMENT ON COLUMN demo.expense_lines.line_no IS '明細行番号';
COMMENT ON COLUMN demo.expense_lines.transaction_date IS '利用日（JST）';
COMMENT ON COLUMN demo.expense_lines.category_ja IS '費目（交通費、宿泊費、日当、消耗品費など）';
COMMENT ON COLUMN demo.expense_lines.claimed_amount_yen IS '申請金額（円）';
COMMENT ON COLUMN demo.expense_lines.approved_amount_yen IS '承認済み金額（円。未査定はNULL）';
COMMENT ON COLUMN demo.expense_lines.source_ref IS 'データ出典';

-- 15 claim_approval_events
COMMENT ON TABLE demo.claim_approval_events IS '精算書の承認イベント。状態変化1回につき1行。差戻し・再申請・最終承認・承認取消に対応';
COMMENT ON COLUMN demo.claim_approval_events.event_id IS '承認イベントID';
COMMENT ON COLUMN demo.claim_approval_events.claim_id IS '精算書番号';
COMMENT ON COLUMN demo.claim_approval_events.event_seq IS 'イベント順序（同一精算書内の連番。同時刻の並び順にも使用）';
COMMENT ON COLUMN demo.claim_approval_events.occurred_at IS 'イベント発生日時（UTC保存）';
COMMENT ON COLUMN demo.claim_approval_events.action IS '承認アクション（SUBMIT=申請、RETURN=差戻し、RESUBMIT=再申請、APPROVE_STAGE=段階承認、FINAL_APPROVE=最終承認、REVOKE=承認取消）';
COMMENT ON COLUMN demo.claim_approval_events.actor_role IS '処理者の役割（申請者、部門承認者、経理、財務）';
COMMENT ON COLUMN demo.claim_approval_events.stage IS '承認段階（0=申請者、1以上=承認ステップ）';
COMMENT ON COLUMN demo.claim_approval_events.is_valid IS '有効区分（true=有効、false=誤登録として無効化。削除はしない）';
COMMENT ON COLUMN demo.claim_approval_events.comment_ja IS '処理コメント';
COMMENT ON COLUMN demo.claim_approval_events.source_ref IS 'データ出典';

-- 16 payment_entries
COMMENT ON TABLE demo.payment_entries IS '支払記録。支払または取消（組戻し）1件につき1行。取消は元の支払を参照する';
COMMENT ON COLUMN demo.payment_entries.entry_id IS '支払記録ID';
COMMENT ON COLUMN demo.payment_entries.claim_id IS '精算書番号';
COMMENT ON COLUMN demo.payment_entries.entry_type IS '支払区分（PAYMENT=支払、REVERSAL=取消・組戻し）';
COMMENT ON COLUMN demo.payment_entries.status IS '支払状態（SCHEDULED=支払予定、SETTLED=支払確定、FAILED=支払失敗、CANCELLED=取消済み）';
COMMENT ON COLUMN demo.payment_entries.settled_at IS '支払確定日時（UTC保存。確定時のみ）';
COMMENT ON COLUMN demo.payment_entries.amount_yen IS '金額（円・符号付き。支払=正、取消=負）';
COMMENT ON COLUMN demo.payment_entries.reverses_entry_id IS '取消対象の支払記録ID（取消のみ）';
COMMENT ON COLUMN demo.payment_entries.source_event_id IS '銀行・支払システムの元イベントID（一意）';
COMMENT ON COLUMN demo.payment_entries.source_ref IS 'データ出典';
