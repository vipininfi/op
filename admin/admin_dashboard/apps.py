from django.apps import AppConfig

class AdminDashboardConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'admin_dashboard'

    def ready(self):
        try:
            from django.contrib.auth.models import User  # ✅ Moved inside
            if not User.objects.filter(username="admin").exists():
                User.objects.create_superuser(
                    username="admin",
                    email="vipin@gmail.com",
                    password="vipin123"
                )
                print("✅ Superuser 'admin' created.")
        except Exception as e:
            print(f"⚠️ Superuser creation skipped: {e}")
