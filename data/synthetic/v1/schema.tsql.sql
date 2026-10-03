-- DB-Mind fictional maintenance schema v1

EXEC(N'CREATE SCHEMA [demo]');

CREATE TABLE [demo].[sites] (
    site_id int NOT NULL,
    site_name nvarchar(80) NOT NULL,
    region nvarchar(30) NOT NULL,
    PRIMARY KEY (site_id),
    UNIQUE (site_name)
);

CREATE TABLE [demo].[technicians] (
    technician_id int NOT NULL,
    display_name nvarchar(80) NOT NULL,
    site_id int NOT NULL,
    specialty nvarchar(30) NOT NULL,
    PRIMARY KEY (technician_id),
    FOREIGN KEY (site_id) REFERENCES [demo].[sites] (site_id)
);

CREATE INDEX ix_technicians_site_id ON [demo].[technicians] (site_id);

CREATE TABLE [demo].[equipment] (
    equipment_id int NOT NULL,
    site_id int NOT NULL,
    asset_code nvarchar(30) NOT NULL,
    equipment_type nvarchar(30) NOT NULL,
    commissioned_at datetime2(0) NOT NULL,
    status nvarchar(20) NOT NULL,
    PRIMARY KEY (equipment_id),
    UNIQUE (asset_code),
    FOREIGN KEY (site_id) REFERENCES [demo].[sites] (site_id),
    CHECK (status IN ('active', 'standby', 'retired'))
);

CREATE INDEX ix_equipment_site_id ON [demo].[equipment] (site_id);

CREATE TABLE [demo].[spare_parts] (
    part_id int NOT NULL,
    part_name nvarchar(80) NOT NULL,
    unit_cost_eur_cents int NOT NULL,
    stock_quantity int NOT NULL,
    PRIMARY KEY (part_id),
    UNIQUE (part_name),
    CHECK (unit_cost_eur_cents >= 0),
    CHECK (stock_quantity >= 0)
);

CREATE TABLE [demo].[work_orders] (
    work_order_id int NOT NULL,
    equipment_id int NOT NULL,
    technician_id int NULL,
    work_type nvarchar(20) NOT NULL,
    priority nvarchar(10) NOT NULL,
    status nvarchar(20) NOT NULL,
    opened_at datetime2(0) NOT NULL,
    due_at datetime2(0) NOT NULL,
    completed_at datetime2(0) NULL,
    labor_minutes int NULL,
    PRIMARY KEY (work_order_id),
    UNIQUE (work_order_id, equipment_id),
    FOREIGN KEY (equipment_id) REFERENCES [demo].[equipment] (equipment_id),
    FOREIGN KEY (technician_id) REFERENCES [demo].[technicians] (technician_id),
    CHECK (work_type IN ('preventive', 'corrective', 'inspection')),
    CHECK (priority IN ('low', 'normal', 'high', 'critical')),
    CHECK (status IN ('open', 'in_progress', 'completed', 'cancelled')),
    CHECK (due_at >= opened_at),
    CHECK ((status = 'completed' AND completed_at IS NOT NULL AND completed_at >= opened_at AND labor_minutes IS NOT NULL AND labor_minutes >= 0) OR (status <> 'completed' AND completed_at IS NULL AND labor_minutes IS NULL))
);

CREATE INDEX ix_work_orders_equipment_id_opened_at ON [demo].[work_orders] (equipment_id, opened_at);

CREATE INDEX ix_work_orders_status_due_at ON [demo].[work_orders] (status, due_at);

CREATE INDEX ix_work_orders_technician_id ON [demo].[work_orders] (technician_id);

CREATE TABLE [demo].[failure_events] (
    failure_id int NOT NULL,
    equipment_id int NOT NULL,
    work_order_id int NULL,
    occurred_at datetime2(0) NOT NULL,
    failure_mode nvarchar(30) NOT NULL,
    downtime_minutes int NULL,
    repair_minutes int NULL,
    PRIMARY KEY (failure_id),
    FOREIGN KEY (equipment_id) REFERENCES [demo].[equipment] (equipment_id),
    FOREIGN KEY (work_order_id, equipment_id) REFERENCES [demo].[work_orders] (work_order_id, equipment_id),
    CHECK (failure_mode IN ('mechanical', 'electrical', 'sensor', 'software')),
    CHECK ((downtime_minutes IS NULL AND repair_minutes IS NULL) OR (downtime_minutes IS NOT NULL AND repair_minutes IS NOT NULL AND repair_minutes >= 0 AND downtime_minutes >= repair_minutes))
);

CREATE INDEX ix_failure_events_equipment_id_occurred_at ON [demo].[failure_events] (equipment_id, occurred_at);

CREATE INDEX ix_failure_events_work_order_id ON [demo].[failure_events] (work_order_id);

CREATE TABLE [demo].[work_order_parts] (
    work_order_id int NOT NULL,
    part_id int NOT NULL,
    quantity int NOT NULL,
    unit_cost_eur_cents int NOT NULL,
    PRIMARY KEY (work_order_id, part_id),
    FOREIGN KEY (work_order_id) REFERENCES [demo].[work_orders] (work_order_id),
    FOREIGN KEY (part_id) REFERENCES [demo].[spare_parts] (part_id),
    CHECK (quantity > 0),
    CHECK (unit_cost_eur_cents >= 0)
);

CREATE INDEX ix_work_order_parts_part_id ON [demo].[work_order_parts] (part_id);

CREATE TABLE [demo].[maintenance_plans] (
    plan_id int NOT NULL,
    equipment_id int NOT NULL,
    task_name nvarchar(80) NOT NULL,
    interval_days int NOT NULL,
    next_due_at datetime2(0) NOT NULL,
    PRIMARY KEY (plan_id),
    UNIQUE (equipment_id, task_name),
    FOREIGN KEY (equipment_id) REFERENCES [demo].[equipment] (equipment_id),
    CHECK (interval_days > 0)
);

CREATE INDEX ix_maintenance_plans_next_due_at ON [demo].[maintenance_plans] (next_due_at);

CREATE TABLE [demo].[safety_incidents] (
    incident_id int NOT NULL,
    equipment_id int NOT NULL,
    occurred_at datetime2(0) NOT NULL,
    severity nvarchar(20) NOT NULL,
    summary nvarchar(200) NOT NULL,
    PRIMARY KEY (incident_id),
    FOREIGN KEY (equipment_id) REFERENCES [demo].[equipment] (equipment_id),
    CHECK (severity IN ('minor', 'major'))
);

CREATE INDEX ix_safety_incidents_equipment_id ON [demo].[safety_incidents] (equipment_id);
