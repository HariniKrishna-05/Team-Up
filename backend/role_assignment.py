import pandas as pd
from db_connection import get_connection


def get_role(frontend, backend, communication, leadership):
    """
    Decide role based on highest skill
    """

    skills = {
        "Frontend Developer": frontend,
        "Backend Developer": backend,
        "Communication Lead": communication,
        "Project Manager": leadership
    }

    return max(skills, key=skills.get)


def assign_roles():

    conn = get_connection()
    cursor = conn.cursor()

    # ✅ Get valid hackathons only (remove NULL)
    hackathons = pd.read_sql("""
        SELECT DISTINCT HACKATHON_PREFERENCE
        FROM HACKATHON_STUDENTS
        WHERE HACKATHON_PREFERENCE IS NOT NULL
    """, conn)

    # extra safety
    hackathons = hackathons.dropna(subset=['HACKATHON_PREFERENCE'])

    for hackathon in hackathons['HACKATHON_PREFERENCE']:

        hackathon = str(hackathon).strip()

        if hackathon == "":
            continue

        print(f"\nProcessing Hackathon: {hackathon}")

        df = pd.read_sql("""
            SELECT STUDENT_ID,
                   FRONTEND_SKILL,
                   BACKEND_SKILL,
                   COMMUNICATION_SKILL,
                   LEADERSHIP_SKILL
            FROM HACKATHON_STUDENTS
            WHERE HACKATHON_PREFERENCE = :hack
        """, conn, params={"hack": hackathon})

        if df.empty:
            continue

        # ✅ Assign roles
        for _, row in df.iterrows():

            role = get_role(
                row['FRONTEND_SKILL'],
                row['BACKEND_SKILL'],
                row['COMMUNICATION_SKILL'],
                row['LEADERSHIP_SKILL']
            )

            cursor.execute("""
                UPDATE HACKATHON_STUDENTS
                SET ROLE = :role
                WHERE STUDENT_ID = :id
            """, {
                "role": role,
                "id": int(row['STUDENT_ID'])
            })

    conn.commit()
    cursor.close()
    conn.close()

    print("\n✅ Roles Assigned Successfully")


if __name__ == "__main__":
    assign_roles()