"""Demo principals and grants (seeded into authz.* by scripts/seed_authz.py; also used by the reference model)."""
PRINCIPALS = [
    # principal_id, display_name_ja, role, employee_id, demo_user, label
    ("P-FIN01", "山崎 葵（経理部・財務）", "FINANCE", "E0020", "fin01", "財務（全社の出張精算）"),
    ("P-HR01", "木村 蓮（人事部）", "HR", "E0017", "hr01", "人事（全社の勤怠証跡）"),
    ("P-MGR-SAL", "中村 さくら（営業部 部門経理）", "DEPT_MANAGER", "E0008", "mgr_sales", "部門経理（営業部）"),
    ("P-MGR-ENG", "井上 恵（開発部 部門経理）", "DEPT_MANAGER", "E0016", "mgr_dev", "部門経理（開発部）"),
    ("P-EMP01", "佐藤 一郎（営業部）", "EMPLOYEE", "E0001", "emp01", "一般社員（本人のみ）"),
]
GRANTS = [
    ("P-FIN01", "CLAIMS", "COMPANY", "C001"),
    ("P-HR01", "ATTENDANCE", "COMPANY", "C001"),
    ("P-MGR-SAL", "CLAIMS", "DEPARTMENT", "D100"), ("P-MGR-SAL", "ATTENDANCE", "DEPARTMENT", "D100"),
    ("P-MGR-ENG", "CLAIMS", "DEPARTMENT", "D200"), ("P-MGR-ENG", "ATTENDANCE", "DEPARTMENT", "D200"),
    ("P-EMP01", "CLAIMS", "EMPLOYEE", "E0001"), ("P-EMP01", "ATTENDANCE", "EMPLOYEE", "E0001"),
]
