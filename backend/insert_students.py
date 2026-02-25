import pandas as pd
from db_connection import get_connection

def ensure_tables(cursor):
    """Create the HACKATHON_STUDENTS table if it doesn't already exist.
    This mirrors the DDL in database/01_create_tables.sql so the script can
    be run standalone without requiring a separate step.
    """
    cursor.execute("""
        SELECT COUNT(*)
        FROM user_tables
        WHERE table_name = 'HACKATHON_STUDENTS'
    """)
    exists = cursor.fetchone()[0]
    if exists == 0:
        ddl = """
        CREATE TABLE HACKATHON_STUDENTS (
            STUDENT_ID NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            STUDENT_NAME VARCHAR2(100),
            EMAIL_ID VARCHAR2(100),
            PASS_WORD VARCHAR2(255),
            COLLEGE_NAME VARCHAR2(150),
            HACKATHON_PREFERENCE VARCHAR2(100),
            FRONTEND_SKILL NUMBER,
            BACKEND_SKILL NUMBER,
            COMMUNICATION_SKILL NUMBER,
            LEADERSHIP_SKILL NUMBER,
            CLUSTER_ID NUMBER,
            TEAM_ID NUMBER
        )
        """
        cursor.execute(ddl)
        cursor.execute("ALTER TABLE HACKATHON_STUDENTS ADD ROLE VARCHAR2(50)")
        # commit will be done by caller


def insert_students():
    # Load CSV
    df = pd.read_csv("teamup(1).csv")

    conn = get_connection()
    cursor = conn.cursor()

    # make sure the target table exists before we start inserting rows
    ensure_tables(cursor)

    for _, row in df.iterrows():
        cursor.execute("""
            INSERT INTO HACKATHON_STUDENTS
            (STUDENT_NAME, EMAIL_ID, PASS_WORD, COLLEGE_NAME,
             HACKATHON_PREFERENCE,
             FRONTEND_SKILL, BACKEND_SKILL,
             COMMUNICATION_SKILL, LEADERSHIP_SKILL)
            VALUES (:1,:2,:3,:4,:5,:6,:7,:8,:9)
        """, (
            row['Student_Name'],
            row['Email_ID'],
            row['Pass_word'],
            row['College_Name'],
            row['Hackathon_Preference'],
            row['Frontend_Skill'],
            row['Backend_Skill'],
            row['Communication_Skill'],
            row['Leadership_Skill']
        ))

    conn.commit()
    cursor.close()
    conn.close()

    print("Students Inserted Successfully ")


if __name__ == "__main__":
    insert_students()
