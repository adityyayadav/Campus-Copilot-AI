"""Create the relational schema, the read-only role, and seed realistic synthetic data.

Run:  python -m scripts.setup_db
"""
import random
import sys
from datetime import date, time, timedelta
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import config  # noqa: E402

random.seed(42)

DEPTS = [
    ("CSE", "Computer Science and Engineering", "Dr. Meera Joshi", "Block A"),
    ("IT", "Information Technology", "Dr. Sanjay Deshmukh", "Block B"),
    ("ENTC", "Electronics and Telecommunication", "Dr. Kavita Rao", "Block C"),
    ("MECH", "Mechanical Engineering", "Dr. Arvind Kulkarni", "Block D"),
    ("CIVIL", "Civil Engineering", "Dr. Neha Bhosale", "Block E"),
]
PREFIX = {"CSE": "CS", "IT": "IT", "ENTC": "ET", "MECH": "ME", "CIVIL": "CV"}

COURSE_TITLES = {
    "CSE": {1: ["Programming Fundamentals", "Engineering Mathematics I", "Digital Logic"],
            2: ["Data Structures", "Discrete Mathematics", "Computer Organization"],
            3: ["Database Management Systems", "Operating Systems", "Computer Networks"],
            4: ["Machine Learning", "Distributed Systems", "Compiler Design"]},
    "IT": {1: ["Python Programming", "Engineering Mathematics I", "Basic Electronics"],
           2: ["Object Oriented Programming", "Data Structures", "Web Technologies"],
           3: ["Software Engineering", "Cloud Computing", "Information Security"],
           4: ["Big Data Analytics", "DevOps", "Mobile Computing"]},
    "ENTC": {1: ["Basic Electrical Engineering", "Engineering Mathematics I", "Engineering Physics"],
             2: ["Analog Circuits", "Signals and Systems", "Network Theory"],
             3: ["Digital Signal Processing", "Microcontrollers", "Communication Systems"],
             4: ["VLSI Design", "Embedded Systems", "Wireless Networks"]},
    "MECH": {1: ["Engineering Mechanics", "Engineering Mathematics I", "Engineering Graphics"],
             2: ["Thermodynamics", "Strength of Materials", "Manufacturing Processes"],
             3: ["Heat Transfer", "Machine Design", "Fluid Mechanics"],
             4: ["Robotics", "Finite Element Analysis", "Automobile Engineering"]},
    "CIVIL": {1: ["Engineering Mechanics", "Engineering Mathematics I", "Building Materials"],
              2: ["Surveying", "Fluid Mechanics", "Structural Analysis I"],
              3: ["Geotechnical Engineering", "Transportation Engineering", "Concrete Technology"],
              4: ["Earthquake Engineering", "Environmental Engineering", "Construction Management"]},
}
ELECTIVES = [  # (course_id, title, dept, year, capacity)
    ("CS351", "Generative AI and LLM Applications", "CSE", 3, 40),
    ("CS352", "Blockchain Technology", "CSE", 3, 35),
    ("IT351", "Data Visualization", "IT", 3, 30),
    ("ET351", "IoT System Design", "ENTC", 3, 30),
]
RESEARCH = {
    "CSE": ["Machine Learning", "Natural Language Processing", "Computer Vision", "Distributed Systems"],
    "IT": ["Cyber Security", "Cloud Computing", "Data Engineering", "Human Computer Interaction"],
    "ENTC": ["VLSI", "Signal Processing", "Wireless Communication", "Embedded Systems"],
    "MECH": ["Robotics", "Thermal Engineering", "Additive Manufacturing", "CAD/CAM"],
    "CIVIL": ["Structural Engineering", "Geotechnics", "Water Resources", "Transportation Planning"],
}
FIRST = ["Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh", "Ayaan", "Krishna", "Ishaan",
         "Ananya", "Diya", "Saanvi", "Aadhya", "Pari", "Anika", "Navya", "Myra", "Sara", "Ira",
         "Rohan", "Kabir", "Om", "Yash", "Tanvi", "Sneha", "Pooja", "Neel", "Riya", "Kunal"]
LAST = ["Sharma", "Patil", "Kulkarni", "Deshpande", "Joshi", "Gupta", "Iyer", "Reddy", "Nair", "Shinde",
        "Pawar", "Mehta", "Verma", "Chavan", "Gaikwad", "Rao", "Bhat", "Kadam", "Jain", "Singh"]

TERM = "2026-ODD"
TERM_START = date(2026, 7, 15)


def name():
    return f"{random.choice(FIRST)} {random.choice(LAST)}"


def build_data():
    data = {k: [] for k in ["departments", "faculty", "students", "courses", "enrollments", "timetable",
                            "exams", "fee_ledger", "research_grants", "grant_applications", "scholarships"]}
    data["departments"] = DEPTS

    # ---- faculty
    fid = 100
    fac_by_dept = {}
    for d, *_ in DEPTS:
        fac_by_dept[d] = []
        for i, area in enumerate(RESEARCH[d]):
            fid += 1
            n = name()
            desig = ["Professor", "Associate Professor", "Assistant Professor", "Assistant Professor"][i]
            row = (f"F{fid}", f"Dr. {n}", d, desig, area, f"{n.split()[0].lower()}.{fid}@campus.edu")
            data["faculty"].append(row)
            fac_by_dept[d].append(row[0])

    # ---- students (hand-crafted demo personas first)
    personas = [
        ("S1023", "Aarav Sharma", "CSE", 3, 3.80, 82.5, True, False),
        ("S1045", "Priya Kulkarni", "IT", 2, 3.20, 71.0, False, False),
        ("S1101", "Rohan Patil", "MECH", 4, 2.90, 88.0, True, False),
        ("S1150", "Ananya Iyer", "CSE", 2, 3.95, 91.0, True, False),
        ("S1177", "Kabir Mehta", "CSE", 3, 3.60, 79.0, False, True),
    ]
    used = {p[0] for p in personas}
    students = [(*p, f"{p[0].lower()}@campus.edu") for p in personas]
    sid = 1000
    for d, *_ in DEPTS:
        for yr in range(1, 5):
            for _ in range(24):
                sid += 1
                while f"S{sid}" in used:
                    sid += 1
                s = f"S{sid}"
                used.add(s)
                gpa = round(min(4.0, max(1.6, random.gauss(3.1, 0.45))), 2)
                att = round(min(99, max(55, random.gauss(81, 8))), 1)
                students.append((s, name(), d, yr, gpa, att, random.random() < 0.45,
                                 random.random() < 0.03, f"{s.lower()}@campus.edu"))
    data["students"] = students

    # ---- courses
    for d, *_ in DEPTS:
        for yr in range(1, 5):
            for i, title in enumerate(COURSE_TITLES[d][yr], start=1):
                cid = f"{PREFIX[d]}{yr}0{i}"
                cap = random.choice([30, 35, 40])
                data["courses"].append((cid, title, d, yr, 4 if i < 3 else 3, TERM, cap, "CORE"))
    for cid, title, d, yr, cap in ELECTIVES:
        data["courses"].append((cid, title, d, yr, 3, TERM, cap, "ELECTIVE"))
    data["courses"].append(("CS401X", "Advanced Deep Learning", "CSE", 4, 3, "2027-EVEN", 30, "ELECTIVE"))

    # ---- enrollments: every student in core courses of their dept-year; electives for some 3rd years
    core = {(c[2], c[3]): [] for c in data["courses"] if c[7] == "CORE"}
    for c in data["courses"]:
        if c[7] == "CORE":
            core[(c[2], c[3])].append(c[0])
    for s in students:
        for cid in core[(s[2], s[3])]:
            data["enrollments"].append((s[0], cid, TERM, None))
    elective_fill = {"CS351": 38, "CS352": 21, "IT351": 30, "ET351": 17}
    for cid, title, d, yr, cap in ELECTIVES:
        pool = [s[0] for s in students if s[3] in (3, 4) and s[2] in (d, "IT" if d == "CSE" else d)]
        random.shuffle(pool)
        if cid == "CS351" and "S1023" in pool:
            pool.remove("S1023")
            pool.insert(0, "S1023")
        for s in pool[: elective_fill[cid]]:
            data["enrollments"].append((s, cid, TERM, None))

    # ---- timetable (3 slots per course this term)
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    starts = [time(9, 0), time(10, 0), time(11, 15), time(13, 30), time(14, 30), time(15, 45)]
    for c in data["courses"]:
        if c[5] != TERM:
            continue
        fac = random.choice(fac_by_dept[c[2]])
        room = f"{c[2][0]}-{random.randint(101, 412)}"
        for day in random.sample(days[:5], 3):
            st = random.choice(starts)
            end = time(st.hour + 1, st.minute)
            data["timetable"].append((c[0], fac, day, st, end, room, TERM))

    # ---- exams: mid-sem done, end-sem in December
    for i, c in enumerate([c for c in data["courses"] if c[5] == TERM]):
        data["exams"].append((c[0], "MID_SEM", date(2026, 9, 14) + timedelta(days=i % 6), time(10, 0), 90,
                              f"Exam Hall {1 + i % 4}"))
        data["exams"].append((c[0], "END_SEM", date(2026, 12, 7) + timedelta(days=(i % 12)), time(10, 0), 180,
                              f"Exam Hall {1 + i % 4}"))

    # ---- fee ledger
    comps = [("TUITION", 95000, date(2026, 8, 1)), ("DEVELOPMENT", 10000, date(2026, 8, 1)),
             ("LIBRARY", 1500, date(2026, 8, 1)), ("EXAM", 2500, date(2026, 11, 15))]
    special = {  # (student, fee_type) -> amount_paid
        ("S1023", "HOSTEL"): 40000, ("S1023", "EXAM"): 0,
        ("S1045", "TUITION"): 50000,
        ("S1101", "HOSTEL"): 0,
    }
    for s in students:
        rows = list(comps) + ([("HOSTEL", 60000, date(2026, 8, 15))] if s[6] else [])
        for ft, amt, due in rows:
            if (s[0], ft) in special:
                paid = special[(s[0], ft)]
            elif s[0] in {p[0] for p in personas}:
                paid = amt
            else:
                r = random.random()
                paid = amt if r < 0.85 else (round(amt * random.choice([0.25, 0.5]), -2) if r < 0.93 else 0)
            status = "PAID" if paid >= amt else ("PARTIAL" if paid > 0 else "PENDING")
            data["fee_ledger"].append((s[0], TERM, ft, amt, paid, due, status))

    # ---- research grants (eligibility per tier lives in the policy document!)
    data["research_grants"] = [
        ("RG-2601", "AI for Early Disease Detection", "DST-SERB", "A", "CSE", 75000, 12, date(2026, 12, 15), "OPEN", "Machine Learning in Healthcare"),
        ("RG-2602", "Trustworthy Large Language Models", "Google Research India", "A", None, 100000, 12, date(2027, 1, 10), "OPEN", "AI Safety and NLP"),
        ("RG-2603", "Edge Computing for Smart Campus", "Dell Technologies", "B", "CSE", 50000, 6, date(2026, 12, 31), "OPEN", "Edge and IoT Systems"),
        ("RG-2604", "Open Source Software Fellowship", "University Innovation Cell", "C", None, 20000, 6, date(2026, 12, 5), "OPEN", "Open Source Contribution"),
        ("RG-2605", "Quantum Algorithms Exploration", "IBM Quantum", "A", "CSE", 80000, 9, date(2026, 11, 20), "CLOSED", "Quantum Computing"),
        ("RG-2606", "Cyber Threat Intelligence", "CERT-In", "B", "IT", 45000, 6, date(2027, 1, 20), "OPEN", "Cyber Security"),
        ("RG-2607", "Low Power VLSI Design", "Intel India", "A", "ENTC", 70000, 12, date(2027, 2, 1), "OPEN", "VLSI"),
        ("RG-2608", "Sustainable Construction Materials", "L&T", "B", "CIVIL", 40000, 9, date(2027, 1, 31), "OPEN", "Green Materials"),
        ("RG-2609", "Autonomous Warehouse Robots", "Tata Motors", "A", "MECH", 65000, 12, date(2026, 12, 20), "OPEN", "Robotics"),
        ("RG-2610", "Data for Social Good", "NITI Aayog", "C", None, 15000, 4, date(2027, 2, 28), "OPEN", "Public Data Analytics"),
        ("RG-2611", "Green Energy Analytics", "Tata Power", "B", None, 35000, 6, date(2027, 1, 15), "OPEN", "Energy and Climate"),
        ("RG-2612", "Computer Vision for Agriculture", "ICAR", "B", "CSE", 40000, 6, date(2026, 10, 31), "CLOSED", "Computer Vision"),
    ]
    data["grant_applications"] = [
        ("RG-2612", "S1023", date(2026, 10, 20), "REJECTED"),
        ("RG-2601", "S1150", date(2026, 11, 2), "SUBMITTED"),
        ("RG-2602", "S1177", date(2026, 11, 10), "UNDER_REVIEW"),
        ("RG-2609", "S1101", date(2026, 11, 5), "SUBMITTED"),
    ]
    data["scholarships"] = [
        ("SCH-01", "Chancellor's Merit Scholarship", "MERIT", 60000, date(2026, 12, 10), 20, "OPEN"),
        ("SCH-02", "Need-Based Financial Aid", "NEED_BASED", 50000, date(2026, 12, 31), 50, "OPEN"),
        ("SCH-03", "Women in STEM Scholarship", "WOMEN_IN_STEM", 40000, date(2027, 1, 15), 25, "OPEN"),
        ("SCH-04", "Sports Excellence Award", "SPORTS", 30000, date(2026, 11, 30), 10, "OPEN"),
        ("SCH-05", "Alumni Endowment Scholarship", "MERIT", 25000, date(2026, 10, 31), 15, "CLOSED"),
        ("SCH-06", "First Generation Learner Grant", "NEED_BASED", 35000, date(2027, 2, 15), 30, "OPEN"),
    ]
    return data


INSERTS = {
    "departments": "INSERT INTO departments VALUES (%s,%s,%s,%s)",
    "faculty": "INSERT INTO faculty VALUES (%s,%s,%s,%s,%s,%s)",
    "students": "INSERT INTO students VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
    "courses": "INSERT INTO courses VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
    "enrollments": "INSERT INTO enrollments VALUES (%s,%s,%s,%s)",
    "timetable": "INSERT INTO timetable (course_id,faculty_id,day_of_week,start_time,end_time,room,term) VALUES (%s,%s,%s,%s,%s,%s,%s)",
    "exams": "INSERT INTO exams (course_id,exam_type,exam_date,start_time,duration_min,venue) VALUES (%s,%s,%s,%s,%s,%s)",
    "fee_ledger": "INSERT INTO fee_ledger (student_id,term,fee_type,amount_due,amount_paid,due_date,status) VALUES (%s,%s,%s,%s,%s,%s,%s)",
    "research_grants": "INSERT INTO research_grants VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
    "grant_applications": "INSERT INTO grant_applications (grant_id,student_id,applied_on,status) VALUES (%s,%s,%s,%s)",
    "scholarships": "INSERT INTO scholarships VALUES (%s,%s,%s,%s,%s,%s,%s)",
}


def main():
    schema_sql = (config.ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
    data = build_data()
    with psycopg.connect(config.DATABASE_URL, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(schema_sql)
        for table, sql in INSERTS.items():
            with conn.cursor() as cur:
                cur.executemany(sql, data[table])
            print(f"  {table:<20} {len(data[table]):>5} rows")

        # Read-only role for the SQL agent: SELECT on public only, no access to the rag schema.
        ru = config.READER_USER
        pw = "'" + config.READER_PASSWORD.replace("'", "''") + "'"
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (ru,)).fetchone()
        verb = "ALTER" if exists else "CREATE"
        conn.execute(f'{verb} ROLE "{ru}" WITH LOGIN PASSWORD {pw}')
        dbname = conn.execute("SELECT current_database()").fetchone()[0]
        conn.execute(f'GRANT CONNECT ON DATABASE "{dbname}" TO "{ru}"')
        conn.execute(f'GRANT USAGE ON SCHEMA public TO "{ru}"')
        conn.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{ru}"')
        # Column-level privilege: contact details are never readable by the SQL agent.
        conn.execute(f'REVOKE SELECT ON students FROM "{ru}"')
        conn.execute(f'GRANT SELECT (student_id, name, dept_id, year, gpa, attendance_pct, is_hosteller, '
                     f'disciplinary_flag) ON students TO "{ru}"')
        # Row-level security: financial / application records are visible only for the signed-in student.
        # The app sets app.student_id / app.role per transaction; the LLM cannot (set_config is blocked).
        # Roles: admin = every row · student = own rows · guest = no personal rows.
        for t in ("fee_ledger", "grant_applications"):
            conn.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
            conn.execute(f"DROP POLICY IF EXISTS own_rows ON {t}")
            conn.execute(f"""CREATE POLICY own_rows ON {t} FOR SELECT TO "{ru}"
                             USING (current_setting('app.role', true) = 'admin'
                                    OR student_id = current_setting('app.student_id', true))""")
        conn.execute(f'ALTER ROLE "{ru}" SET default_transaction_read_only = on')
        conn.execute(f'ALTER ROLE "{ru}" SET statement_timeout = 5000')
        conn.execute("CREATE SCHEMA IF NOT EXISTS rag")
        conn.execute(f'REVOKE ALL ON SCHEMA rag FROM "{ru}"')
        print(f"  read-only role '{ru}' ready")
    print("Database ready.")


if __name__ == "__main__":
    main()
