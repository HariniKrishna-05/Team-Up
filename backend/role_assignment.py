import pandas as pd

def get_role(frontend, backend, communication, leadership):
    """Determine role based on highest skill"""
    skills = {
        "Frontend Developer": frontend,
        "Backend Developer": backend,
        "Communication Lead": communication,
        "Project Manager": leadership
    }
    return max(skills, key=skills.get)


def assign_roles(cursor, connection):
    """Assign roles to students based on their highest skill"""
    
    hackathons = pd.read_sql("""
        SELECT DISTINCT HACKATHON_PREFERENCE
        FROM HACKATHON_STUDENTS
    """, connection)

    for hackathon in hackathons['HACKATHON_PREFERENCE']:

        print(f"\nAssigning roles for: {hackathon}")

        df = pd.read_sql("""
            SELECT STUDENT_ID,
                   FRONTEND_SKILL,
                   BACKEND_SKILL,
                   COMMUNICATION_SKILL,
                   LEADERSHIP_SKILL
            FROM HACKATHON_STUDENTS
            WHERE HACKATHON_PREFERENCE = :hack
        """, connection, params={"hack": hackathon})

        if df.empty:
            continue

        # Assign roles
        for _, row in df.iterrows():

            role = get_role(
                row['FRONTEND_SKILL'],
                row['BACKEND_SKILL'],
                row['COMMUNICATION_SKILL'],
                row['LEADERSHIP_SKILL']
            )

            cursor.execute("""
                UPDATE HACKATHON_STUDENTS
                SET ROLE = :1
                WHERE STUDENT_ID = :2
            """, (role, int(row['STUDENT_ID'])))

    connection.commit()
    print("Roles assigned successfully")


