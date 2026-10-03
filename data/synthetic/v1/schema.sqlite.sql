-- DB-Mind fictional maintenance schema v1

CREATE TABLE sites (
    site_id INTEGER NOT NULL,
    site_name TEXT NOT NULL,
    region TEXT NOT NULL,
    PRIMARY KEY (site_id),
    UNIQUE (site_name)
);

CREATE TABLE technicians (
    technician_id INTEGER NOT NULL,
    display_name TEXT NOT NULL,
    site_id INTEGER NOT NULL,
    specialty TEXT NOT NULL,
    PRIMARY KEY (technician_id),
    FOREIGN KEY (site_id) REFERENCES sites (site_id)
);

CREATE INDEX ix_technicians_site_id ON technicians (site_id);

CREATE TABLE equipment (
    equipment_id INTEGER NOT NULL,
    site_id INTEGER NOT NULL,
    asset_code TEXT NOT NULL,
    equipment_type TEXT NOT NULL,
    commissioned_at TEXT NOT NULL,
    status TEXT NOT NULL,
    PRIMARY KEY (equipment_id),
    UNIQUE (asset_code),
    FOREIGN KEY (site_id) REFERENCES sites (site_id),
    CHECK (status IN ('active', 'standby', 'retired'))
);

CREATE INDEX ix_equipment_site_id ON equipment (site_id);

CREATE TABLE spare_parts (
    part_id INTEGER NOT NULL,
    part_name TEXT NOT NULL,
    unit_cost_eur_cents INTEGER NOT NULL,
    stock_quantity INTEGER NOT NULL,
    PRIMARY KEY (part_id),
    UNIQUE (part_name),
    CHECK (unit_cost_eur_cents >= 0),
    CHECK (stock_quantity >= 0)
);

CREATE TABLE work_orders (
    work_order_id INTEGER NOT NULL,
    equipment_id INTEGER NOT NULL,
    technician_id INTEGER NULL,
    work_type TEXT NOT NULL,
    priority TEXT NOT NULL,
    status TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    due_at TEXT NOT NULL,
    completed_at TEXT NULL,
    labor_minutes INTEGER NULL,
    PRIMARY KEY (work_order_id),
    UNIQUE (work_order_id, equipment_id),
    FOREIGN KEY (equipment_id) REFERENCES equipment (equipment_id),
    FOREIGN KEY (technician_id) REFERENCES technicians (technician_id),
    CHECK (work_type IN ('preventive', 'corrective', 'inspection')),
    CHECK (priority IN ('low', 'normal', 'high', 'critical')),
    CHECK (status IN ('open', 'in_progress', 'completed', 'cancelled')),
    CHECK (due_at >= opened_at),
    CHECK ((status = 'completed' AND completed_at IS NOT NULL AND completed_at >= opened_at AND labor_minutes IS NOT NULL AND labor_minutes >= 0) OR (status <> 'completed' AND completed_at IS NULL AND labor_minutes IS NULL))
);

CREATE INDEX ix_work_orders_equipment_id_opened_at ON work_orders (equipment_id, opened_at);

CREATE INDEX ix_work_orders_status_due_at ON work_orders (status, due_at);

CREATE INDEX ix_work_orders_technician_id ON work_orders (technician_id);

CREATE TABLE failure_events (
    failure_id INTEGER NOT NULL,
    equipment_id INTEGER NOT NULL,
    work_order_id INTEGER NULL,
    occurred_at TEXT NOT NULL,
    failure_mode TEXT NOT NULL,
    downtime_minutes INTEGER NULL,
    repair_minutes INTEGER NULL,
    PRIMARY KEY (failure_id),
    FOREIGN KEY (equipment_id) REFERENCES equipment (equipment_id),
    FOREIGN KEY (work_order_id, equipment_id) REFERENCES work_orders (work_order_id, equipment_id),
    CHECK (failure_mode IN ('mechanical', 'electrical', 'sensor', 'software')),
    CHECK ((downtime_minutes IS NULL AND repair_minutes IS NULL) OR (downtime_minutes IS NOT NULL AND repair_minutes IS NOT NULL AND repair_minutes >= 0 AND downtime_minutes >= repair_minutes))
);

CREATE INDEX ix_failure_events_equipment_id_occurred_at ON failure_events (equipment_id, occurred_at);

CREATE INDEX ix_failure_events_work_order_id ON failure_events (work_order_id);

CREATE TABLE work_order_parts (
    work_order_id INTEGER NOT NULL,
    part_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    unit_cost_eur_cents INTEGER NOT NULL,
    PRIMARY KEY (work_order_id, part_id),
    FOREIGN KEY (work_order_id) REFERENCES work_orders (work_order_id),
    FOREIGN KEY (part_id) REFERENCES spare_parts (part_id),
    CHECK (quantity > 0),
    CHECK (unit_cost_eur_cents >= 0)
);

CREATE INDEX ix_work_order_parts_part_id ON work_order_parts (part_id);

CREATE TABLE maintenance_plans (
    plan_id INTEGER NOT NULL,
    equipment_id INTEGER NOT NULL,
    task_name TEXT NOT NULL,
    interval_days INTEGER NOT NULL,
    next_due_at TEXT NOT NULL,
    PRIMARY KEY (plan_id),
    UNIQUE (equipment_id, task_name),
    FOREIGN KEY (equipment_id) REFERENCES equipment (equipment_id),
    CHECK (interval_days > 0)
);

CREATE INDEX ix_maintenance_plans_next_due_at ON maintenance_plans (next_due_at);

CREATE TABLE safety_incidents (
    incident_id INTEGER NOT NULL,
    equipment_id INTEGER NOT NULL,
    occurred_at TEXT NOT NULL,
    severity TEXT NOT NULL,
    summary TEXT NOT NULL,
    PRIMARY KEY (incident_id),
    FOREIGN KEY (equipment_id) REFERENCES equipment (equipment_id),
    CHECK (severity IN ('minor', 'major'))
);

CREATE INDEX ix_safety_incidents_equipment_id ON safety_incidents (equipment_id);
