"""Create the aircraft_mx database, load realistic sample data and create the read-only user.

Run once (uses the MYSQL_ADMIN_* credentials from .env):
    python db/setup_db.py            # create if missing
    python db/setup_db.py --reset    # drop and rebuild
"""
import argparse
import os
import random
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pymysql

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nl2sql.config import load_settings  # noqa: E402

random.seed(42)  # deterministic data so demo questions give repeatable answers
TODAY = date.today()

OPERATORS = ["IndiGo", "Air India", "Akasa Air", "SpiceJet", "Alliance Air"]
BASES = ["DEL", "BOM", "BLR", "MAA", "HYD", "CCU"]
FLEET_TYPES = [
    ("A320neo", "Airbus"), ("A321neo", "Airbus"), ("B737-800", "Boeing"),
    ("B737 MAX 8", "Boeing"), ("ATR 72-600", "ATR"), ("E175", "Embraer"),
]
# (name, category, manufacturer, part_number prefix, life limit hours or None)
COMPONENT_TEMPLATES = [
    ("CFM LEAP-1A Engine", "Engine", "CFM International", "LEAP1A", 25000),
    ("PW127M Engine", "Engine", "Pratt & Whitney", "PW127", 20000),
    ("Main Landing Gear", "Landing Gear", "Safran", "MLG", 30000),
    ("Nose Landing Gear", "Landing Gear", "Safran", "NLG", 30000),
    ("Brake Assembly", "Landing Gear", "Collins Aerospace", "BRK", 3000),
    ("Flight Management Computer", "Avionics", "Honeywell", "FMC", None),
    ("Weather Radar", "Avionics", "Collins Aerospace", "WXR", None),
    ("Air Data Inertial Reference Unit", "Avionics", "Honeywell", "ADIRU", None),
    ("Hydraulic Pump", "Hydraulics", "Parker Aerospace", "HYDP", 12000),
    ("Hydraulic Accumulator", "Hydraulics", "Parker Aerospace", "HYDA", 15000),
    ("Auxiliary Power Unit", "APU", "Honeywell", "APU131", 18000),
    ("Integrated Drive Generator", "Electrical", "Collins Aerospace", "IDG", 10000),
    ("Main Battery", "Electrical", "Saft", "BAT", 6000),
    ("Wing Slat Actuator", "Airframe", "Moog", "SLAT", 20000),
    ("Cabin Pressure Controller", "Airframe", "Liebherr", "CPC", None),
]
SPECIALIZATION_FOR = {
    "Engine": "Engines", "Landing Gear": "Landing Gear", "Avionics": "Avionics",
    "Hydraulics": "Hydraulics", "APU": "Engines", "Electrical": "Electrical", "Airframe": "Airframe",
}
ATA_FOR = {
    "Engine": "ATA-72", "Landing Gear": "ATA-32", "Avionics": "ATA-34", "Hydraulics": "ATA-29",
    "APU": "ATA-49", "Electrical": "ATA-24", "Airframe": "ATA-27",
}
LOG_DESCRIPTIONS = {
    "Repair": "Repaired {c} after reported fault",
    "Replacement": "Replaced {c} with serviceable unit",
    "Lubrication": "Lubricated {c} per AMM task",
    "Adjustment": "Adjusted {c} rigging / tolerances",
    "Overhaul": "Sent {c} for shop overhaul",
    "Software Update": "Loaded latest software on {c}",
    "Troubleshooting": "Troubleshot intermittent fault on {c}",
}
FINDINGS = [
    "Minor corrosion found, treated and monitored",
    "Hydraulic seep within limits",
    "Brake wear at 70%, plan replacement",
    "Fan blade nick blended within limits",
    "Loose connector re-secured",
    "Tyre pressure low, serviced",
    "Crack indication detected, component rejected",
]
FIRST = ["Arjun", "Priya", "Rahul", "Ananya", "Vikram", "Sneha", "Karthik", "Meera", "Rohan",
         "Divya", "Aditya", "Lakshmi", "Suresh", "Kavya", "Nikhil", "Pooja", "Manoj", "Isha"]
LAST = ["Sharma", "Iyer", "Reddy", "Nair", "Patel", "Gupta", "Menon", "Rao", "Singh", "Das"]


def rand_date(start: date, end: date) -> date:
    return start + timedelta(days=random.randint(0, max((end - start).days, 0)))


def generate_data():
    aircraft = []
    for i in range(1, 26):
        model, mfr = random.choice(FLEET_TYPES)
        status = random.choices(["Active", "In Maintenance", "Grounded", "Retired"], [75, 15, 7, 3])[0]
        aircraft.append((i, f"VT-R{chr(64 + (i - 1) // 26 + 1)}{chr(65 + (i - 1) % 26)}", model, mfr,
                         random.randint(2010, 2024), random.choice(OPERATORS), random.choice(BASES),
                         random.randint(4000, 60000), status))

    technicians = []
    for i in range(1, 21):
        technicians.append((i, f"{random.choice(FIRST)} {random.choice(LAST)}", f"AME-{10400 + i}",
                            random.choice(sorted(set(SPECIALIZATION_FOR.values()))), random.choice(BASES),
                            rand_date(date(2008, 1, 1), date(2024, 6, 30))))

    components = []
    cid = 0
    for ac in aircraft:
        is_turboprop = ac[3] == "ATR"
        for name, cat, mfr, pn, life in COMPONENT_TEMPLATES:
            if (name.startswith("CFM") and is_turboprop) or (name.startswith("PW127") and not is_turboprop):
                continue
            if random.random() < 0.25:  # not every aircraft tracks every part
                continue
            cid += 1
            hours = random.randint(100, int((life or 15000) * 1.05))
            if life and hours >= life:
                status = random.choice(["Unserviceable", "Due for Inspection"])
            else:
                status = random.choices(["Serviceable", "Due for Inspection", "Unserviceable"], [80, 15, 5])[0]
            components.append((cid, ac[0], f"{pn}-{random.randint(100, 999)}", f"SN{100000 + cid}", name, cat,
                               mfr, rand_date(date(2015, 1, 1), TODAY - timedelta(days=30)), hours, life, status))
    for _ in range(12):  # spares in stores, not installed
        cid += 1
        name, cat, mfr, pn, life = random.choice(COMPONENT_TEMPLATES)
        components.append((cid, None, f"{pn}-{random.randint(100, 999)}", f"SN{100000 + cid}", name, cat,
                           mfr, None, random.randint(0, 2000), life, "Removed"))

    installed = [c for c in components if c[1] is not None]
    techs_by_spec = {}
    for t in technicians:
        techs_by_spec.setdefault(t[3], []).append(t[0])

    def tech_for(category):
        return random.choice(techs_by_spec.get(SPECIALIZATION_FOR[category]) or [t[0] for t in technicians])

    inspections = []
    for iid in range(1, 701):
        ac = random.choice(aircraft)
        comps = [c for c in installed if c[1] == ac[0]]
        comp = random.choice(comps) if comps and random.random() < 0.6 else None
        itype = random.choice(["NDT", "Borescope", "Visual"]) if comp else \
            random.choices(["A-Check", "B-Check", "C-Check", "D-Check", "Visual"], [45, 20, 15, 5, 15])[0]
        scheduled = rand_date(TODAY - timedelta(days=540), TODAY + timedelta(days=60))
        if scheduled <= TODAY and random.random() < 0.9:
            completed = min(scheduled + timedelta(days=random.randint(0, 5)), TODAY)
            result = random.choices(["Pass", "Pass with Findings", "Fail"], [70, 22, 8])[0]
            tech = tech_for(comp[5]) if comp else random.choice(technicians)[0]
            findings = random.choice(FINDINGS) if result != "Pass" else None
        else:  # future, or overdue
            completed, result, tech, findings = None, "Pending", None, None
        inspections.append((iid, ac[0], comp[0] if comp else None, tech, itype, scheduled, completed,
                            result, findings))

    logs = []
    for lid in range(1, 1601):
        comp = random.choice(installed)
        action = random.choices(list(LOG_DESCRIPTIONS), [25, 15, 15, 10, 5, 10, 20])[0]
        labor = round(random.uniform(0.5, 4) if action in ("Lubrication", "Adjustment", "Software Update")
                      else random.uniform(2, 36), 1)
        cost = 0 if action in ("Lubrication", "Adjustment", "Software Update", "Troubleshooting") else \
            round(random.uniform(500, 250000 if comp[5] == "Engine" else 40000), 2)
        logs.append((lid, comp[1], comp[0], tech_for(comp[5]), rand_date(TODAY - timedelta(days=730), TODAY),
                     action, ATA_FOR[comp[5]], LOG_DESCRIPTIONS[action].format(c=comp[4].lower()), labor, cost,
                     random.random() < 0.08))
    return aircraft, technicians, components, inspections, logs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="drop and recreate the database")
    args = parser.parse_args()

    admin = load_settings(admin=True)
    reader_user = os.getenv("MYSQL_READONLY_USER", "nl2sql_reader")
    reader_pass = os.getenv("MYSQL_READONLY_PASSWORD", "")
    if not re.fullmatch(r"\w+", admin.database) or not re.fullmatch(r"\w+", reader_user):
        sys.exit("MYSQL_DATABASE and MYSQL_READONLY_USER must be plain identifiers")
    if not reader_pass:
        sys.exit("Set MYSQL_READONLY_PASSWORD in .env first")

    conn = pymysql.connect(host=admin.host, port=admin.port, user=admin.user, password=admin.password,
                           autocommit=True)
    db = admin.database
    with conn.cursor() as cur:
        if args.reset:
            cur.execute(f"DROP DATABASE IF EXISTS `{db}`")
        cur.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name = %s", (db,))
        if cur.fetchone():
            print(f"Database '{db}' already exists - use --reset to rebuild it. Refreshing user grants only.")
        else:
            cur.execute(f"CREATE DATABASE `{db}` CHARACTER SET utf8mb4")
            cur.execute(f"USE `{db}`")
            schema_sql = (Path(__file__).parent / "schema.sql").read_text()
            schema_sql = re.sub(r"^\s*--.*$", "", schema_sql, flags=re.M)  # strip comment lines
            # Split only on semicolons that end a line - column comments may contain ';'.
            for stmt in filter(str.strip, re.split(r";\s*$", schema_sql, flags=re.M)):
                cur.execute(stmt)

            aircraft, technicians, components, inspections, logs = generate_data()
            cur.executemany("INSERT INTO aircraft VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", aircraft)
            cur.executemany("INSERT INTO technicians VALUES (%s,%s,%s,%s,%s,%s)", technicians)
            cur.executemany("INSERT INTO components VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", components)
            cur.executemany("INSERT INTO inspections VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", inspections)
            cur.executemany("INSERT INTO maintenance_logs VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", logs)
            print(f"Loaded {len(aircraft)} aircraft, {len(technicians)} technicians, {len(components)} components, "
                  f"{len(inspections)} inspections, {len(logs)} maintenance logs into '{db}'.")

        # Defence in depth: the app's account can only SELECT, so even a query that slipped past
        # the validator could not modify data.
        for host in ("localhost", "127.0.0.1"):
            cur.execute(f"CREATE USER IF NOT EXISTS '{reader_user}'@'{host}' IDENTIFIED BY %s", (reader_pass,))
            cur.execute(f"ALTER USER '{reader_user}'@'{host}' IDENTIFIED BY %s", (reader_pass,))
            cur.execute(f"GRANT SELECT ON `{db}`.* TO '{reader_user}'@'{host}'")
        print(f"Read-only user '{reader_user}' has SELECT on '{db}'.")
    conn.close()


if __name__ == "__main__":
    main()
