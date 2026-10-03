"""Original fictional records; no source data or current clock is consulted."""
from datetime import datetime, timedelta
import hashlib
import json
import random

SEED = 20261001
REFERENCE_DATE = "2026-10-01T00:00:00"


def generate():
    rng = random.Random(SEED)
    reference = datetime.fromisoformat(REFERENCE_DATE)
    result = {}
    result["sites"] = [(i, name, region) for i, (name, region) in enumerate([
        ("Fictional Aurora", "North"), ("Fictional Linden", "South"),
        ("Fictional Möbius", "East"), ("Fictional Cedar", "West")], 1)]
    result["technicians"] = [(i, f"Fictional Technician {i:02d}", (i - 1) // 3 + 1,
                             ["mechanical", "electrical", "general"][(i - 1) % 3])
                            for i in range(1, 13)]
    result["equipment"] = [(i, (i - 1) // 15 + 1, f"SYN-EQ-{i:03d}",
                            ["pump", "conveyor", "compressor"][(i - 1) % 3],
                            (reference - timedelta(days=400 + i * 11)).isoformat(),
                            "standby" if i % 15 == 0 else "active") for i in range(1, 61)]
    result["spare_parts"] = [(i, f"Fictional {name} {i:02d}", 500 + i * 375, 5 + i * 3)
                             for i, name in enumerate(["bearing", "belt", "seal", "relay", "sensor", "filter"] * 3, 1)]
    orders, failures, parts = [], [], []
    for i in range(1, 1201):
        equipment = rng.randint(1, 60)
        site = (equipment - 1) // 15 + 1
        technician = None if i % 7 == 0 else (site - 1) * 3 + rng.randint(1, 3)
        opened = reference - timedelta(days=rng.randint(2, 180), hours=rng.randint(0, 23))
        status = "completed" if i % 10 < 7 else ["open", "in_progress", "cancelled"][i % 10 - 7]
        work_type = ["corrective", "preventive", "inspection"][i % 3]
        # Last four assets intentionally have no corrective failures.
        if equipment > 56 and work_type == "corrective":
            work_type = "preventive"
        completed = opened + timedelta(hours=rng.randint(1, 36)) if status == "completed" else None
        labor = rng.randint(15, 240) if completed else None
        orders.append((i, equipment, technician, work_type,
                       ["low", "normal", "high", "critical"][i % 4], status,
                       opened.isoformat(), (opened + timedelta(days=3)).isoformat(),
                       completed.isoformat() if completed else None, labor))
        if work_type == "corrective" and status != "cancelled":
            repair = rng.randint(10, 180) if completed else None
            downtime = repair + rng.randint(0, 300) if completed else None
            failures.append((len(failures) + 1, equipment, i, opened.isoformat(),
                             ["mechanical", "electrical", "sensor", "software"][i % 4], downtime, repair))
        if completed:
            for part_id in sorted(rng.sample(range(1, 19), rng.randint(1, 3))):
                parts.append((i, part_id, rng.randint(1, 4), 500 + part_id * 375))
    result["work_orders"] = orders
    result["failure_events"] = failures
    result["work_order_parts"] = parts
    result["maintenance_plans"] = [(i, i, "Fictional routine inspection", 30,
                                    (reference + timedelta(days=i % 21 - 7)).isoformat())
                                   for i in range(1, 61)]
    result["safety_incidents"] = []
    return result


def fingerprint(data):
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()
