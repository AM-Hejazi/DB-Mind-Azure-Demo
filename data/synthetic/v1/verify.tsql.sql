-- DB-Mind synthetic v1 read-only verification; no live run recorded.

SET NOCOUNT ON;

IF DB_NAME() NOT LIKE N'dbmind[_]synthetic[_]%' THROW 51010, 'Wrong synthetic database', 1;

IF SCHEMA_ID(N'demo') IS NULL THROW 51011, 'Missing demo schema', 1;

IF (SELECT COUNT_BIG(*) FROM [demo].[sites]) <> 4 THROW 51012, 'Row count mismatch: sites', 1;

IF (SELECT COUNT_BIG(*) FROM [demo].[technicians]) <> 12 THROW 51012, 'Row count mismatch: technicians', 1;

IF (SELECT COUNT_BIG(*) FROM [demo].[equipment]) <> 60 THROW 51012, 'Row count mismatch: equipment', 1;

IF (SELECT COUNT_BIG(*) FROM [demo].[spare_parts]) <> 18 THROW 51012, 'Row count mismatch: spare_parts', 1;

IF (SELECT COUNT_BIG(*) FROM [demo].[work_orders]) <> 1200 THROW 51012, 'Row count mismatch: work_orders', 1;

IF (SELECT COUNT_BIG(*) FROM [demo].[failure_events]) <> 339 THROW 51012, 'Row count mismatch: failure_events', 1;

IF (SELECT COUNT_BIG(*) FROM [demo].[work_order_parts]) <> 1697 THROW 51012, 'Row count mismatch: work_order_parts', 1;

IF (SELECT COUNT_BIG(*) FROM [demo].[maintenance_plans]) <> 60 THROW 51012, 'Row count mismatch: maintenance_plans', 1;

IF (SELECT COUNT_BIG(*) FROM [demo].[safety_incidents]) <> 0 THROW 51012, 'Row count mismatch: safety_incidents', 1;

IF (SELECT COUNT(*) FROM sys.foreign_keys WHERE schema_id=SCHEMA_ID(N'demo')) <> 10 THROW 51013, 'Foreign key count mismatch', 1;

IF EXISTS (SELECT 1 FROM sys.foreign_keys WHERE schema_id=SCHEMA_ID(N'demo') AND (is_disabled=1 OR is_not_trusted=1)) THROW 51014, 'Disabled/untrusted foreign key', 1;

IF EXISTS (SELECT 1 FROM sys.check_constraints WHERE schema_id=SCHEMA_ID(N'demo') AND (is_disabled=1 OR is_not_trusted=1)) THROW 51015, 'Disabled/untrusted check constraint', 1;

PRINT N'Counts and enabled/trusted constraints passed; compare query results below.';

PRINT N'status_counts: expected [["cancelled", 120], ["completed", 840], ["in_progress", 120], ["open", 120]]';

SELECT status, COUNT(*) AS work_order_count FROM [demo].work_orders GROUP BY status ORDER BY status;

PRINT N'downtime_ranking: expected [["SYN-EQ-005", 2757], ["SYN-EQ-055", 2522], ["SYN-EQ-029", 2092], ["SYN-EQ-010", 1917], ["SYN-EQ-020", 1866]]';

SELECT TOP (5) e.asset_code, SUM(f.downtime_minutes) AS downtime_minutes FROM [demo].equipment e JOIN [demo].failure_events f ON f.equipment_id=e.equipment_id WHERE f.downtime_minutes IS NOT NULL GROUP BY e.asset_code ORDER BY downtime_minutes DESC, e.asset_code;

PRINT N'september_labor: expected [["Fictional Aurora", 3788], ["Fictional Cedar", 6156], ["Fictional Linden", 3644], ["Fictional Möbius", 3489]]';

SELECT s.site_name, SUM(w.labor_minutes) AS labor_minutes FROM [demo].sites s JOIN [demo].equipment e ON e.site_id=s.site_id JOIN [demo].work_orders w ON w.equipment_id=e.equipment_id WHERE w.completed_at >= '2026-09-01T00:00:00' AND w.completed_at < '2026-10-01T00:00:00' GROUP BY s.site_name ORDER BY s.site_name;

PRINT N'overdue: expected [["Fictional Aurora", 59], ["Fictional Cedar", 57], ["Fictional Linden", 65], ["Fictional Möbius", 58]]';

SELECT s.site_name, COUNT(*) AS overdue_orders FROM [demo].sites s JOIN [demo].equipment e ON e.site_id=s.site_id JOIN [demo].work_orders w ON w.equipment_id=e.equipment_id WHERE w.status IN ('open','in_progress') AND w.due_at < '2026-10-01T00:00:00' GROUP BY s.site_name ORDER BY s.site_name;

PRINT N'no_failures: expected [["SYN-EQ-057"], ["SYN-EQ-058"], ["SYN-EQ-059"], ["SYN-EQ-060"]]';

SELECT e.asset_code FROM [demo].equipment e WHERE NOT EXISTS (SELECT 1 FROM [demo].failure_events f WHERE f.equipment_id=e.equipment_id) ORDER BY e.asset_code;

PRINT N'parts_cost: expected [["Fictional Aurora", 4684750], ["Fictional Cedar", 4673500], ["Fictional Linden", 4014000], ["Fictional Möbius", 4122125]]';

SELECT s.site_name, SUM(wp.quantity * wp.unit_cost_eur_cents) AS parts_cost_eur_cents FROM [demo].sites s JOIN [demo].equipment e ON e.site_id=s.site_id JOIN [demo].work_orders w ON w.equipment_id=e.equipment_id JOIN [demo].work_order_parts wp ON wp.work_order_id=w.work_order_id WHERE w.status='completed' GROUP BY s.site_name ORDER BY s.site_name;

PRINT N'technician_ranking: expected [["Fictional Technician 02", 73], ["Fictional Technician 05", 68], ["Fictional Technician 10", 68], ["Fictional Technician 12", 65], ["Fictional Technician 09", 64]]';

SELECT TOP (5) t.display_name, COUNT(*) AS completed_orders FROM [demo].technicians t JOIN [demo].work_orders w ON w.technician_id=t.technician_id WHERE w.status='completed' GROUP BY t.display_name ORDER BY completed_orders DESC, t.display_name;

PRINT N'mttr: expected [["electrical", 59, 100.14], ["mechanical", 75, 95.57], ["sensor", 74, 94.39], ["software", 55, 92.82]]';

SELECT failure_mode, COUNT(*) AS resolved_failures, ROUND(AVG(CAST(repair_minutes AS FLOAT)), 2) AS mttr_minutes FROM [demo].failure_events WHERE repair_minutes IS NOT NULL GROUP BY failure_mode ORDER BY failure_mode;

PRINT N'upcoming_plans: expected [["Fictional Aurora", 7], ["Fictional Cedar", 7], ["Fictional Linden", 3], ["Fictional Möbius", 4]]';

SELECT s.site_name, COUNT(*) AS plans_due FROM [demo].sites s JOIN [demo].equipment e ON e.site_id=s.site_id JOIN [demo].maintenance_plans m ON m.equipment_id=e.equipment_id WHERE m.next_due_at >= '2026-10-01T00:00:00' AND m.next_due_at < '2026-10-08T00:00:00' GROUP BY s.site_name ORDER BY s.site_name;

PRINT N'empty_incidents: expected []';

SELECT incident_id, equipment_id, occurred_at, severity, summary FROM [demo].safety_incidents ORDER BY incident_id;

PRINT N'unassigned: expected [[35]]';

SELECT COUNT(*) AS unassigned_orders FROM [demo].work_orders WHERE technician_id IS NULL AND status IN ('open','in_progress');
