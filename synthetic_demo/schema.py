"""Version 1 domain definitions shared by SQLite and SQL Server renderers."""
from dataclasses import dataclass
import re

VERSION = 1
APPLICATION_ID = 0x44424D53


@dataclass(frozen=True)
class Column:
    name: str
    sqlserver_type: str
    sqlite_type: str
    nullable: bool = False


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]
    constraints: tuple[str, ...]
    indexes: tuple[tuple[str, ...], ...] = ()


def integer(name, nullable=False):
    return Column(name, "int", "INTEGER", nullable)


def text(name, size, nullable=False):
    return Column(name, f"nvarchar({size})", "TEXT", nullable)


def timestamp(name, nullable=False):
    return Column(name, "datetime2(0)", "TEXT", nullable)


TABLES = (
    Table("sites", (integer("site_id"), text("site_name", 80), text("region", 30)),
          ("PRIMARY KEY (site_id)", "UNIQUE (site_name)")),
    Table("technicians", (integer("technician_id"), text("display_name", 80),
          integer("site_id"), text("specialty", 30)),
          ("PRIMARY KEY (technician_id)", "FOREIGN KEY (site_id) REFERENCES sites (site_id)"),
          (("site_id",),)),
    Table("equipment", (integer("equipment_id"), integer("site_id"), text("asset_code", 30),
          text("equipment_type", 30), timestamp("commissioned_at"), text("status", 20)),
          ("PRIMARY KEY (equipment_id)", "UNIQUE (asset_code)",
           "FOREIGN KEY (site_id) REFERENCES sites (site_id)",
           "CHECK (status IN ('active', 'standby', 'retired'))"), (("site_id",),)),
    Table("spare_parts", (integer("part_id"), text("part_name", 80),
          integer("unit_cost_eur_cents"), integer("stock_quantity")),
          ("PRIMARY KEY (part_id)", "UNIQUE (part_name)",
           "CHECK (unit_cost_eur_cents >= 0)", "CHECK (stock_quantity >= 0)")),
    Table("work_orders", (integer("work_order_id"), integer("equipment_id"),
          integer("technician_id", True), text("work_type", 20), text("priority", 10),
          text("status", 20), timestamp("opened_at"), timestamp("due_at"),
          timestamp("completed_at", True), integer("labor_minutes", True)),
          ("PRIMARY KEY (work_order_id)", "UNIQUE (work_order_id, equipment_id)",
           "FOREIGN KEY (equipment_id) REFERENCES equipment (equipment_id)",
           "FOREIGN KEY (technician_id) REFERENCES technicians (technician_id)",
           "CHECK (work_type IN ('preventive', 'corrective', 'inspection'))",
           "CHECK (priority IN ('low', 'normal', 'high', 'critical'))",
           "CHECK (status IN ('open', 'in_progress', 'completed', 'cancelled'))",
           "CHECK (due_at >= opened_at)",
           "CHECK ((status = 'completed' AND completed_at IS NOT NULL AND completed_at >= opened_at AND labor_minutes IS NOT NULL AND labor_minutes >= 0) OR (status <> 'completed' AND completed_at IS NULL AND labor_minutes IS NULL))"),
          (("equipment_id", "opened_at"), ("status", "due_at"), ("technician_id",))),
    Table("failure_events", (integer("failure_id"), integer("equipment_id"),
          integer("work_order_id", True), timestamp("occurred_at"), text("failure_mode", 30),
          integer("downtime_minutes", True), integer("repair_minutes", True)),
          ("PRIMARY KEY (failure_id)",
           "FOREIGN KEY (equipment_id) REFERENCES equipment (equipment_id)",
           "FOREIGN KEY (work_order_id, equipment_id) REFERENCES work_orders (work_order_id, equipment_id)",
           "CHECK (failure_mode IN ('mechanical', 'electrical', 'sensor', 'software'))",
           "CHECK ((downtime_minutes IS NULL AND repair_minutes IS NULL) OR (downtime_minutes IS NOT NULL AND repair_minutes IS NOT NULL AND repair_minutes >= 0 AND downtime_minutes >= repair_minutes))"),
          (("equipment_id", "occurred_at"), ("work_order_id",))),
    Table("work_order_parts", (integer("work_order_id"), integer("part_id"),
          integer("quantity"), integer("unit_cost_eur_cents")),
          ("PRIMARY KEY (work_order_id, part_id)",
           "FOREIGN KEY (work_order_id) REFERENCES work_orders (work_order_id)",
           "FOREIGN KEY (part_id) REFERENCES spare_parts (part_id)",
           "CHECK (quantity > 0)", "CHECK (unit_cost_eur_cents >= 0)"), (("part_id",),)),
    Table("maintenance_plans", (integer("plan_id"), integer("equipment_id"),
          text("task_name", 80), integer("interval_days"), timestamp("next_due_at")),
          ("PRIMARY KEY (plan_id)", "UNIQUE (equipment_id, task_name)",
           "FOREIGN KEY (equipment_id) REFERENCES equipment (equipment_id)",
           "CHECK (interval_days > 0)"), (("next_due_at",),)),
    Table("safety_incidents", (integer("incident_id"), integer("equipment_id"),
          timestamp("occurred_at"), text("severity", 20), text("summary", 200)),
          ("PRIMARY KEY (incident_id)",
           "FOREIGN KEY (equipment_id) REFERENCES equipment (equipment_id)",
           "CHECK (severity IN ('minor', 'major'))"), (("equipment_id",),)),
)


def render_schema(dialect):
    """Render only DDL; no connection or execution. SQL Server uses [demo]."""
    if dialect not in {"sqlite", "sqlserver"}:
        raise ValueError("Unsupported dialect")
    statements = [f"-- DB-Mind fictional maintenance schema v{VERSION}"]
    if dialect == "sqlserver":
        statements.append("CREATE SCHEMA [demo];")
    for table in TABLES:
        target = f"[demo].[{table.name}]" if dialect == "sqlserver" else table.name
        columns = [f"    {c.name} {c.sqlserver_type if dialect == 'sqlserver' else c.sqlite_type} "
                   f"{'NULL' if c.nullable else 'NOT NULL'}" for c in table.columns]
        constraints = list(table.constraints)
        if dialect == "sqlserver":
            constraints = [re.sub(r"REFERENCES (\w+)", r"REFERENCES [demo].[\1]", c)
                           for c in constraints]
        statements.append(f"CREATE TABLE {target} (\n" + ",\n".join(columns + ["    " + c for c in constraints]) + "\n);")
        for fields in table.indexes:
            name = "ix_" + table.name + "_" + "_".join(fields)
            statements.append(f"CREATE INDEX {name} ON {target} ({', '.join(fields)});")
    return "\n\n".join(statements) + "\n"
