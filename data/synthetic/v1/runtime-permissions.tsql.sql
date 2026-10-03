-- Run only as a setup/admin identity AFTER the synthetic seed.
IF DB_NAME() NOT LIKE N'dbmind[_]synthetic[_]%' THROW 51020, 'Wrong synthetic database', 1;
IF SCHEMA_ID(N'demo') IS NULL THROW 51021, 'Seed the synthetic schema first', 1;
IF DATABASE_PRINCIPAL_ID(N'dbmind_demo_reader') IS NULL CREATE ROLE [dbmind_demo_reader] AUTHORIZATION [dbo];
GRANT SELECT ON OBJECT::[demo].[sites] TO [dbmind_demo_reader];
GRANT VIEW DEFINITION ON OBJECT::[demo].[sites] TO [dbmind_demo_reader];
GRANT SELECT ON OBJECT::[demo].[technicians] TO [dbmind_demo_reader];
GRANT VIEW DEFINITION ON OBJECT::[demo].[technicians] TO [dbmind_demo_reader];
GRANT SELECT ON OBJECT::[demo].[equipment] TO [dbmind_demo_reader];
GRANT VIEW DEFINITION ON OBJECT::[demo].[equipment] TO [dbmind_demo_reader];
GRANT SELECT ON OBJECT::[demo].[spare_parts] TO [dbmind_demo_reader];
GRANT VIEW DEFINITION ON OBJECT::[demo].[spare_parts] TO [dbmind_demo_reader];
GRANT SELECT ON OBJECT::[demo].[work_orders] TO [dbmind_demo_reader];
GRANT VIEW DEFINITION ON OBJECT::[demo].[work_orders] TO [dbmind_demo_reader];
GRANT SELECT ON OBJECT::[demo].[failure_events] TO [dbmind_demo_reader];
GRANT VIEW DEFINITION ON OBJECT::[demo].[failure_events] TO [dbmind_demo_reader];
GRANT SELECT ON OBJECT::[demo].[work_order_parts] TO [dbmind_demo_reader];
GRANT VIEW DEFINITION ON OBJECT::[demo].[work_order_parts] TO [dbmind_demo_reader];
GRANT SELECT ON OBJECT::[demo].[maintenance_plans] TO [dbmind_demo_reader];
GRANT VIEW DEFINITION ON OBJECT::[demo].[maintenance_plans] TO [dbmind_demo_reader];
GRANT SELECT ON OBJECT::[demo].[safety_incidents] TO [dbmind_demo_reader];
GRANT VIEW DEFINITION ON OBJECT::[demo].[safety_incidents] TO [dbmind_demo_reader];
DENY INSERT, UPDATE, DELETE ON SCHEMA::[demo] TO [dbmind_demo_reader];
DENY ALTER ON SCHEMA::[demo] TO [dbmind_demo_reader];
DENY CREATE TABLE, CREATE VIEW, CREATE PROCEDURE, ALTER ANY SCHEMA TO [dbmind_demo_reader];
DENY EXECUTE TO [dbmind_demo_reader];
-- Provision the runtime USER separately, then ALTER ROLE ... ADD MEMBER as documented.
