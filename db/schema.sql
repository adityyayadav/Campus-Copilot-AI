-- =====================================================================
-- Campus Copilot AI: relational schema (schema "public")
-- Table/column COMMENTs are used to auto-generate the "schema cards"
-- the Text-to-SQL agent retrieves, so keep them descriptive.
-- =====================================================================
DROP TABLE IF EXISTS exams, timetable, enrollments, fee_ledger, grant_applications,
    research_grants, scholarships, courses, students, faculty, departments CASCADE;

CREATE TABLE departments (
    dept_id     TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    hod_name    TEXT NOT NULL,
    building    TEXT NOT NULL
);
COMMENT ON TABLE departments IS 'Academic departments of the university. Students, faculty and courses belong to a department.';
COMMENT ON COLUMN departments.dept_id IS 'Short department code, e.g. CSE = Computer Science and Engineering, IT = Information Technology, ENTC = Electronics and Telecommunication, MECH = Mechanical, CIVIL = Civil';
COMMENT ON COLUMN departments.name IS 'Full department name';
COMMENT ON COLUMN departments.hod_name IS 'Head of department';

CREATE TABLE faculty (
    faculty_id     TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    dept_id        TEXT NOT NULL REFERENCES departments(dept_id),
    designation    TEXT NOT NULL,
    research_area  TEXT NOT NULL,
    email          TEXT NOT NULL
);
COMMENT ON TABLE faculty IS 'Teaching staff. Faculty teach courses (timetable) and act as research mentors.';
COMMENT ON COLUMN faculty.designation IS 'Professor, Associate Professor or Assistant Professor';
COMMENT ON COLUMN faculty.research_area IS 'Main research interest, e.g. Machine Learning, VLSI, Structural Engineering';

CREATE TABLE students (
    student_id        TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    dept_id           TEXT NOT NULL REFERENCES departments(dept_id),
    year              INT  NOT NULL CHECK (year BETWEEN 1 AND 4),
    gpa               NUMERIC(3,2) NOT NULL,
    attendance_pct    NUMERIC(5,2) NOT NULL,
    is_hosteller      BOOLEAN NOT NULL,
    disciplinary_flag BOOLEAN NOT NULL DEFAULT FALSE,
    email             TEXT NOT NULL
);
COMMENT ON TABLE students IS 'One row per enrolled student with academic standing. year = current year of study (1-4).';
COMMENT ON COLUMN students.student_id IS 'Student identifier like S1023';
COMMENT ON COLUMN students.gpa IS 'Cumulative GPA on a 4.0 scale';
COMMENT ON COLUMN students.attendance_pct IS 'Overall attendance percentage for the current term';
COMMENT ON COLUMN students.is_hosteller IS 'TRUE if the student lives in the university hostel';
COMMENT ON COLUMN students.disciplinary_flag IS 'TRUE if the student has an active disciplinary action';

CREATE TABLE courses (
    course_id   TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    dept_id     TEXT NOT NULL REFERENCES departments(dept_id),
    year        INT  NOT NULL,
    credits     INT  NOT NULL,
    term        TEXT NOT NULL,
    capacity    INT  NOT NULL,
    course_type TEXT NOT NULL
);
COMMENT ON TABLE courses IS 'Courses offered. Seats left = capacity minus number of enrollments for that course and term.';
COMMENT ON COLUMN courses.course_id IS 'Course code like CS301';
COMMENT ON COLUMN courses.year IS 'Year of study the course is meant for';
COMMENT ON COLUMN courses.term IS 'Academic term, e.g. 2026-ODD (Jul-Dec 2026) or 2027-EVEN (Jan-May 2027). Current term is 2026-ODD';
COMMENT ON COLUMN courses.course_type IS 'CORE or ELECTIVE';

CREATE TABLE enrollments (
    student_id  TEXT NOT NULL REFERENCES students(student_id),
    course_id   TEXT NOT NULL REFERENCES courses(course_id),
    term        TEXT NOT NULL,
    grade       TEXT,
    PRIMARY KEY (student_id, course_id, term)
);
COMMENT ON TABLE enrollments IS 'Which student is enrolled in which course in which term. Count rows per course to get enrollment counts.';
COMMENT ON COLUMN enrollments.grade IS 'Letter grade (A, A-, B+, B, B-, C+, C, D, F); NULL while the course is in progress';

CREATE TABLE timetable (
    slot_id     SERIAL PRIMARY KEY,
    course_id   TEXT NOT NULL REFERENCES courses(course_id),
    faculty_id  TEXT NOT NULL REFERENCES faculty(faculty_id),
    day_of_week TEXT NOT NULL,
    start_time  TIME NOT NULL,
    end_time    TIME NOT NULL,
    room        TEXT NOT NULL,
    term        TEXT NOT NULL
);
COMMENT ON TABLE timetable IS 'Weekly lecture/lab schedule: which course meets on which day, time and room, and who teaches it.';
COMMENT ON COLUMN timetable.day_of_week IS 'Monday ... Saturday';

CREATE TABLE exams (
    exam_id     SERIAL PRIMARY KEY,
    course_id   TEXT NOT NULL REFERENCES courses(course_id),
    exam_type   TEXT NOT NULL,
    exam_date   DATE NOT NULL,
    start_time  TIME NOT NULL,
    duration_min INT NOT NULL,
    venue       TEXT NOT NULL
);
COMMENT ON TABLE exams IS 'Scheduled examinations for courses.';
COMMENT ON COLUMN exams.exam_type IS 'MID_SEM or END_SEM';

CREATE TABLE fee_ledger (
    txn_id      SERIAL PRIMARY KEY,
    student_id  TEXT NOT NULL REFERENCES students(student_id),
    term        TEXT NOT NULL,
    fee_type    TEXT NOT NULL,
    amount_due  NUMERIC(10,2) NOT NULL,
    amount_paid NUMERIC(10,2) NOT NULL,
    due_date    DATE NOT NULL,
    status      TEXT NOT NULL
);
COMMENT ON TABLE fee_ledger IS 'Live fee ledger: one row per student per fee component per term. Outstanding balance = amount_due - amount_paid. All amounts in INR.';
COMMENT ON COLUMN fee_ledger.fee_type IS 'TUITION, HOSTEL, EXAM, LIBRARY or DEVELOPMENT';
COMMENT ON COLUMN fee_ledger.status IS 'PAID, PARTIAL or PENDING';

CREATE TABLE research_grants (
    grant_id        TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    sponsor         TEXT NOT NULL,
    tier            TEXT NOT NULL,
    dept_id         TEXT REFERENCES departments(dept_id),
    amount          NUMERIC(10,2) NOT NULL,
    duration_months INT NOT NULL,
    deadline        DATE NOT NULL,
    status          TEXT NOT NULL,
    focus_area      TEXT NOT NULL
);
COMMENT ON TABLE research_grants IS 'Undergraduate research grants. Eligibility rules (GPA, year of study) for each tier are defined in the Research Grant Policy document, not in this table.';
COMMENT ON COLUMN research_grants.tier IS 'Grant tier A, B or C. Eligibility thresholds per tier are in the Research Grant Policy';
COMMENT ON COLUMN research_grants.dept_id IS 'Department the grant is restricted to; NULL means open to all departments (interdisciplinary)';
COMMENT ON COLUMN research_grants.amount IS 'Grant amount in INR';
COMMENT ON COLUMN research_grants.deadline IS 'Last date to apply';
COMMENT ON COLUMN research_grants.status IS 'OPEN or CLOSED';

CREATE TABLE grant_applications (
    application_id SERIAL PRIMARY KEY,
    grant_id       TEXT NOT NULL REFERENCES research_grants(grant_id),
    student_id     TEXT NOT NULL REFERENCES students(student_id),
    applied_on     DATE NOT NULL,
    status         TEXT NOT NULL
);
COMMENT ON TABLE grant_applications IS 'Applications submitted by students to research grants.';
COMMENT ON COLUMN grant_applications.status IS 'SUBMITTED, UNDER_REVIEW, AWARDED or REJECTED';

CREATE TABLE scholarships (
    scholarship_id TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    category       TEXT NOT NULL,
    amount         NUMERIC(10,2) NOT NULL,
    deadline       DATE NOT NULL,
    seats          INT NOT NULL,
    status         TEXT NOT NULL
);
COMMENT ON TABLE scholarships IS 'Scholarship schemes with amounts and deadlines. Eligibility criteria and renewal rules are in the Scholarship Guidelines document.';
COMMENT ON COLUMN scholarships.category IS 'MERIT, NEED_BASED, SPORTS or WOMEN_IN_STEM';
COMMENT ON COLUMN scholarships.amount IS 'Annual amount in INR';
COMMENT ON COLUMN scholarships.status IS 'OPEN or CLOSED';

-- =====================================================================
-- Indexes on every foreign key / common filter, so per-student and per-course
-- lookups stay index scans as the university grows (RLS filters on student_id too).
-- =====================================================================
CREATE INDEX idx_students_dept_year      ON students (dept_id, year);
CREATE INDEX idx_faculty_dept            ON faculty (dept_id);
CREATE INDEX idx_courses_dept_term       ON courses (dept_id, term);
CREATE INDEX idx_enrollments_course_term ON enrollments (course_id, term);
CREATE INDEX idx_timetable_course        ON timetable (course_id);
CREATE INDEX idx_timetable_faculty       ON timetable (faculty_id);
CREATE INDEX idx_exams_course            ON exams (course_id);
CREATE INDEX idx_fee_ledger_student_term ON fee_ledger (student_id, term);
CREATE INDEX idx_fee_ledger_status_due   ON fee_ledger (status, due_date);
CREATE INDEX idx_grants_status_deadline  ON research_grants (status, deadline);
CREATE INDEX idx_grant_apps_student      ON grant_applications (student_id);
CREATE INDEX idx_grant_apps_grant        ON grant_applications (grant_id);
CREATE INDEX idx_scholarships_status     ON scholarships (status, deadline);
