from django.apps import AppConfig
from django.contrib.auth.models import User

class AdminDashboardConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'admin_dashboard'

    def ready(self):
        try:
            if not User.objects.filter(username="admin").exists():
                User.objects.create_superuser(
                    username="admin",
                    email="vipin@gmail.com",
                    password="vipin123"
                )
                print("✅ Superuser 'admin' created.")
        except Exception as e:
            print(f"⚠️ Superuser creation skipped: {e}")
