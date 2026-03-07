import pandas as pd
from sklearn.cluster import KMeans
from db_connection import get_connection

def perform_hackathon_clustering():

    conn = get_connection()
    cursor = conn.cursor()

    hackathons = pd.read_sql("""
        SELECT DISTINCT HACKATHON_PREFERENCE
        FROM HACKATHON_STUDENTS
    """, conn)

    for hackathon in hackathons['HACKATHON_PREFERENCE']:

        print("\n====================================")
        print(f"Processing Hackathon: {hackathon}")
        print("====================================")

        df = pd.read_sql(f"""
            SELECT STUDENT_ID,
                   FRONTEND_SKILL,
                   BACKEND_SKILL,
                   COMMUNICATION_SKILL,
                   LEADERSHIP_SKILL
            FROM HACKATHON_STUDENTS
            WHERE HACKATHON_PREFERENCE = '{hackathon}'
        """, conn)

        if df.empty:
            continue

        skills = df[['FRONTEND_SKILL',
                     'BACKEND_SKILL',
                     'COMMUNICATION_SKILL',
                     'LEADERSHIP_SKILL']]

        kmeans = KMeans(n_clusters=4, random_state=42)
        df['CLUSTER_ID'] = kmeans.fit_predict(skills)

        # 🔥 PRINT CENTROIDS
        centroids = kmeans.cluster_centers_

        print("\nCluster Centers:")
        print("Cluster | Frontend | Backend | Communication | Leadership")

        for idx, center in enumerate(centroids):
            print(f"{idx:^7} | "
                  f"{center[0]:^8.2f} | "
                  f"{center[1]:^7.2f} | "
                  f"{center[2]:^13.2f} | "
                  f"{center[3]:^10.2f}")

        # Update database
        for _, row in df.iterrows():
            cursor.execute("""
                UPDATE HACKATHON_STUDENTS
                SET CLUSTER_ID = :1
                WHERE STUDENT_ID = :2
            """, (int(row['CLUSTER_ID']), int(row['STUDENT_ID'])))

    conn.commit()
    cursor.close()
    conn.close()

    print("\nAll Hackathons Clustered Successfully")


if __name__ == "__main__":
    perform_hackathon_clustering()