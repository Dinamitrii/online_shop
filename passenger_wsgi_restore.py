"""
Passenger WSGI entry point — изисква се от cPanel Setup Python App.
Passenger търси точно файл с това име в Application root
и очаква обект 'application' вътре в него.
"""

from app import app as application, db, migrate_db, seed_data

# Инициализация на базата данни при първо зареждане на приложението
with application.app_context():
    db.create_all()
    migrate_db()
    seed_data()