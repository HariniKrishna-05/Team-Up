import pandas as pd
from db_connection import get_connection


def form_balanced_role_teams():

    conn = get_connection()
    cursor = conn.cursor()

    print("\n========== TEAM FORMATION STARTED ==========\n")

    # ✅ Reset teams safely
    cursor.execute("UPDATE HACKATHON_STUDENTS SET TEAM_ID = NULL")
    conn.commit()

    # ✅ Ignore NULL hackathons (MAIN FIX)
    hackathons = pd.read_sql("""
        SELECT DISTINCT HACKATHON_PREFERENCE
        FROM HACKATHON_STUDENTS
        WHERE HACKATHON_PREFERENCE IS NOT NULL
    """, conn)

    # extra pandas safety
    hackathons = hackathons.dropna(subset=['HACKATHON_PREFERENCE'])

    team_id = 1

    for hackathon in hackathons['HACKATHON_PREFERENCE']:

        hackathon = str(hackathon).strip()

        if hackathon == "":
            continue

        print(f"\nProcessing Hackathon: {hackathon}")

        df = pd.read_sql("""
            SELECT STUDENT_ID, ROLE, CLUSTER_ID
            FROM HACKATHON_STUDENTS
            WHERE HACKATHON_PREFERENCE = :hack
        """, conn, params={"hack": hackathon})

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
                    SET TEAM_ID = :team
                    WHERE STUDENT_ID = :sid
                """, {"team": team_id, "sid": sid})

                used_ids.append(sid)

            remaining = remaining[~remaining['STUDENT_ID'].isin(used_ids)]

            team_id += 1

        conn.commit()

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
                    SET TEAM_ID = :team
                    WHERE STUDENT_ID = :sid
                """, {"team": team_id, "sid": sid})

                used_ids.append(sid)

            remaining = remaining[~remaining['STUDENT_ID'].isin(used_ids)]

            team_id += 1

        conn.commit()

    cursor.close()
    conn.close()

    print("\n✅ Teams formed successfully")


if __name__ == "__main__":
    form_balanced_role_teams()
def get_teams_by_hackathon(connection):
    """
    Returns teams grouped by hackathon and team_id
    Used for teams.html display
    """

    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            HACKATHON_PREFERENCE,
            TEAM_ID,
            STUDENT_NAME,
            ROLE
        FROM HACKATHON_STUDENTS
        WHERE TEAM_ID IS NOT NULL
        ORDER BY HACKATHON_PREFERENCE, TEAM_ID
    """)

    rows = cursor.fetchall()

    teams = {}

    for hackathon, team_id, name, role in rows:

        if hackathon not in teams:
            teams[hackathon] = {}

        if team_id not in teams[hackathon]:
            teams[hackathon][team_id] = []

        teams[hackathon][team_id].append({
            "name": name,
            "role": role
        })

    cursor.close()
    return teams