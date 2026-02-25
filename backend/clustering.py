import pandas as pd
from sklearn.cluster import KMeans

def perform_hackathon_clustering(cursor, connection):
    """Cluster students by skills for each hackathon preference"""
    
    hackathons = pd.read_sql("""
        SELECT DISTINCT HACKATHON_PREFERENCE
        FROM HACKATHON_STUDENTS
    """, connection)

    for hackathon in hackathons['HACKATHON_PREFERENCE']:

        print(f"\nClustering: {hackathon}")

        df = pd.read_sql(f"""
            SELECT STUDENT_ID,
                   FRONTEND_SKILL,
                   BACKEND_SKILL,
                   COMMUNICATION_SKILL,
                   LEADERSHIP_SKILL
            FROM HACKATHON_STUDENTS
            WHERE HACKATHON_PREFERENCE = '{hackathon}'
        """, connection)

        if df.empty:
            continue

        skills = df[['FRONTEND_SKILL',
                     'BACKEND_SKILL',
                     'COMMUNICATION_SKILL',
                     'LEADERSHIP_SKILL']]

        kmeans = KMeans(n_clusters=4, random_state=42)
        df['CLUSTER_ID'] = kmeans.fit_predict(skills)

        # Update database
        for _, row in df.iterrows():
            cursor.execute("""
                UPDATE HACKATHON_STUDENTS
                SET CLUSTER_ID = :1
                WHERE STUDENT_ID = :2
            """, (int(row['CLUSTER_ID']), int(row['STUDENT_ID'])))

    connection.commit()
    print("Clustering completed successfully")
