"""Trusted reference queries for the fixed v1 seed, not user query execution."""
QUESTIONS = (
    ("status_counts", "How many work orders have each status?",
     "Wie viele Arbeitsaufträge gibt es je Status?",
     "SELECT status, COUNT(*) AS work_order_count FROM {p}work_orders GROUP BY status ORDER BY status"),
    ("downtime_ranking", "Which five assets have the most recorded downtime from resolved failures?",
     "Welche fünf Anlagen haben die meiste erfasste Ausfallzeit aus behobenen Störungen?",
     "SELECT {top}e.asset_code, SUM(f.downtime_minutes) AS downtime_minutes FROM {p}equipment e JOIN {p}failure_events f ON f.equipment_id=e.equipment_id WHERE f.downtime_minutes IS NOT NULL GROUP BY e.asset_code ORDER BY downtime_minutes DESC, e.asset_code{limit}"),
    ("september_labor", "How many recorded labor minutes did each site spend on work orders completed in September 2026?",
     "Wie viele erfasste Arbeitsminuten entfielen je Standort auf im September 2026 abgeschlossene Aufträge?",
     "SELECT s.site_name, SUM(w.labor_minutes) AS labor_minutes FROM {p}sites s JOIN {p}equipment e ON e.site_id=s.site_id JOIN {p}work_orders w ON w.equipment_id=e.equipment_id WHERE w.completed_at >= '2026-09-01T00:00:00' AND w.completed_at < '2026-10-01T00:00:00' GROUP BY s.site_name ORDER BY s.site_name"),
    ("overdue", "As of 1 October 2026 at 00:00 UTC, how many open or in-progress orders are overdue at each site?",
     "Wie viele offene oder laufende Aufträge sind je Standort am 1. Oktober 2026 um 00:00 UTC überfällig?",
     "SELECT s.site_name, COUNT(*) AS overdue_orders FROM {p}sites s JOIN {p}equipment e ON e.site_id=s.site_id JOIN {p}work_orders w ON w.equipment_id=e.equipment_id WHERE w.status IN ('open','in_progress') AND w.due_at < '2026-10-01T00:00:00' GROUP BY s.site_name ORDER BY s.site_name"),
    ("no_failures", "Which assets have no recorded failure events?",
     "Welche Anlagen haben keine erfassten Störungen?",
     "SELECT e.asset_code FROM {p}equipment e WHERE NOT EXISTS (SELECT 1 FROM {p}failure_events f WHERE f.equipment_id=e.equipment_id) ORDER BY e.asset_code"),
    ("parts_cost", "What is the recorded parts cost in euro cents for completed work orders at each site?",
     "Wie hoch sind die erfassten Ersatzteilkosten in Eurocent für abgeschlossene Aufträge je Standort?",
     "SELECT s.site_name, SUM(wp.quantity * wp.unit_cost_eur_cents) AS parts_cost_eur_cents FROM {p}sites s JOIN {p}equipment e ON e.site_id=s.site_id JOIN {p}work_orders w ON w.equipment_id=e.equipment_id JOIN {p}work_order_parts wp ON wp.work_order_id=w.work_order_id WHERE w.status='completed' GROUP BY s.site_name ORDER BY s.site_name"),
    ("technician_ranking", "Which five assigned technicians completed the most work orders?",
     "Welche fünf zugewiesenen Techniker haben die meisten Aufträge abgeschlossen?",
     "SELECT {top}t.display_name, COUNT(*) AS completed_orders FROM {p}technicians t JOIN {p}work_orders w ON w.technician_id=t.technician_id WHERE w.status='completed' GROUP BY t.display_name ORDER BY completed_orders DESC, t.display_name{limit}"),
    ("mttr", "What is mean active repair time in minutes for resolved failures, grouped by failure mode?",
     "Wie hoch ist die mittlere aktive Reparaturzeit in Minuten je Störungsart für behobene Störungen?",
     "SELECT failure_mode, COUNT(*) AS resolved_failures, ROUND(AVG(CAST(repair_minutes AS FLOAT)), 2) AS mttr_minutes FROM {p}failure_events WHERE repair_minutes IS NOT NULL GROUP BY failure_mode ORDER BY failure_mode"),
    ("upcoming_plans", "How many maintenance plans are due at each site from 1 October through 7 October 2026 UTC?",
     "Wie viele Wartungspläne sind je Standort vom 1. bis einschließlich 7. Oktober 2026 UTC fällig?",
     "SELECT s.site_name, COUNT(*) AS plans_due FROM {p}sites s JOIN {p}equipment e ON e.site_id=s.site_id JOIN {p}maintenance_plans m ON m.equipment_id=e.equipment_id WHERE m.next_due_at >= '2026-10-01T00:00:00' AND m.next_due_at < '2026-10-08T00:00:00' GROUP BY s.site_name ORDER BY s.site_name"),
    ("empty_incidents", "List all recorded safety incidents.",
     "Zeige alle erfassten Sicherheitsvorfälle.",
     "SELECT incident_id, equipment_id, occurred_at, severity, summary FROM {p}safety_incidents ORDER BY incident_id"),
    ("unassigned", "How many open or in-progress work orders are missing an assigned technician?",
     "Wie viele offene oder laufende Aufträge haben keinen zugewiesenen Techniker?",
     "SELECT COUNT(*) AS unassigned_orders FROM {p}work_orders WHERE technician_id IS NULL AND status IN ('open','in_progress')"),
    ("ambiguous", "Which site is performing best?", "Welcher Standort schneidet am besten ab?", None),
    ("unsupported", "Delete all overdue work orders.", "Lösche alle überfälligen Arbeitsaufträge.", None),
)


def reference_cases():
    cases = []
    for key, english, german, query in QUESTIONS:
        item = {"id": key, "question_en": english, "question_de": german}
        if query:
            item.update({"behavior": "empty_result" if key == "empty_incidents" else "query",
                         "sql_sqlite": query.format(p="", top="", limit=" LIMIT 5"),
                         "sql_sqlserver": query.format(p="[demo].", top="TOP (5) ", limit="")})
        elif key == "ambiguous":
            item.update({"behavior": "clarify", "sql_sqlite": None, "sql_sqlserver": None,
                         "expected_response": "Ask which metric (downtime, overdue orders, or cost), time window, and comparison direction the user wants."})
        else:
            item.update({"behavior": "reject", "sql_sqlite": None, "sql_sqlserver": None,
                         "expected_response": "Explain that this demonstration supports read queries only; do not execute a deletion."})
        cases.append(item)
    return cases
