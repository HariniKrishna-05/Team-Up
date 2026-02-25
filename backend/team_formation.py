import pandas as pd

def form_balanced_role_teams(cursor, connection):
    """Form balanced teams based on roles and clusters"""

    print("\n========== TEAM FORMATION STARTED ==========\n")

    # Reset teams
    cursor.execute("UPDATE HACKATHON_STUDENTS SET TEAM_ID = NULL")
    connection.commit()

    hackathons = pd.read_sql("""
        SELECT DISTINCT HACKATHON_PREFERENCE
        FROM HACKATHON_STUDENTS
    """, connection)

    team_id = 1

    for hackathon in hackathons['HACKATHON_PREFERENCE']:

        print(f"\nProcessing Hackathon: {hackathon}")

        df = pd.read_sql("""
            SELECT STUDENT_ID, ROLE, CLUSTER_ID
            FROM HACKATHON_STUDENTS
            WHERE HACKATHON_PREFERENCE = :hack
        """, connection, params={"hack": hackathon})

        if df.empty:
            continue

        remaining = df.copy()

        # ===============================
        # STAGE 1 → Cluster + Role Teams
        # ===============================
        print("Stage 1: Cluster + Role Teams")

        while True:

            team = []
            used_clusters = set()

            roles = [
                'Frontend Developer',
                'Backend Developer',
                'Communication Lead',
                'Project Manager'
            ]

            for role in roles:

                candidates = remaining[
                    (remaining['ROLE'] == role) &
                    (~remaining['CLUSTER_ID'].isin(used_clusters))
                ]

                if candidates.empty:
                    team = []
                    break

                student = candidates.iloc[0]
                team.append(student)
                used_clusters.add(student['CLUSTER_ID'])

            if len(team) < 4:
                break

            used_ids = []

            for member in team:
                sid = int(member['STUDENT_ID'])
                cursor.execute("""
                    UPDATE HACKATHON_STUDENTS
                    SET TEAM_ID = :1
                    WHERE STUDENT_ID = :2
                """, (team_id, sid))
                used_ids.append(sid)

            remaining = remaining[~remaining['STUDENT_ID'].isin(used_ids)]
            team_id += 1

        connection.commit()

        # ===============================
        # STAGE 2 → Remaining Role Only
        # ===============================
        print("Stage 2: Remaining Students (Role Only)")

        while True:

            frontend = remaining[remaining['ROLE'] == 'Frontend Developer']
            backend = remaining[remaining['ROLE'] == 'Backend Developer']
            communication = remaining[remaining['ROLE'] == 'Communication Lead']
            manager = remaining[remaining['ROLE'] == 'Project Manager']

            if min(len(frontend), len(backend), len(communication), len(manager)) == 0:
                break

            team_members = [
                frontend.iloc[0],
                backend.iloc[0],
                communication.iloc[0],
                manager.iloc[0]
            ]

            used_ids = []

            for member in team_members:
                sid = int(member['STUDENT_ID'])
                cursor.execute("""
                    UPDATE HACKATHON_STUDENTS
                    SET TEAM_ID = :1
                    WHERE STUDENT_ID = :2
                """, (team_id, sid))
                used_ids.append(sid)

            remaining = remaining[~remaining['STUDENT_ID'].isin(used_ids)]
            team_id += 1

        connection.commit()

    print("\nTeams formed successfully")


def get_teams_by_hackathon(connection):
    """Retrieve all teams organized by hackathon"""
    
    hackathons = pd.read_sql("""
        SELECT DISTINCT HACKATHON_PREFERENCE
        FROM HACKATHON_STUDENTS
        ORDER BY HACKATHON_PREFERENCE
    """, connection)

    teams_data = {}

    for hackathon in hackathons['HACKATHON_PREFERENCE']:

        df = pd.read_sql("""
            SELECT TEAM_ID,
                   STUDENT_NAME,
                   EMAIL_ID,
                   ROLE
            FROM HACKATHON_STUDENTS
            WHERE HACKATHON_PREFERENCE = :hack
              AND TEAM_ID IS NOT NULL
            ORDER BY TEAM_ID, ROLE
        """, connection, params={"hack": hackathon})

        teams_data[hackathon] = df.to_dict('records')

    return teams_data
