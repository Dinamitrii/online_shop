"""
WSGI entry point за Железария Дианабад.

Използва се от production WSGI сървър (Gunicorn, uWSGI и др.), вместо
вградения dev сървър на Flask (python app.py).

Пример стартиране с Gunicorn:
    gunicorn --bind 0.0.0.0:8000 wsgi:app

С повече workers:
    gunicorn --workers 3 --bind 0.0.0.0:8000 wsgi:app
"""

from app import app, db, migrate_db, seed_data

# Инициализация на базата данни при зареждане на модула
# (изпълнява се веднъж, когато WSGI сървърът стартира процеса).
with app.app_context():
    db.create_all()
    migrate_db()
    seed_data()

if __name__ == '__main__':
    app.run()
