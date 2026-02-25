import oracledb

def get_connection():
    connection = oracledb.connect(
    user="system",
    password="system123",
    dsn="localhost/XE"
    )
    return connection
