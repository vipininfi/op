from django.apps import AppConfig


class HotelBotAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'hotel_bot_app'

    def ready(self):
        try:
            import hotel_bot_app.signals  # Import signals when app is ready
            print("Successfully loaded hotel_bot_app signals")
        except Exception as e:
            print(f"Error loading hotel_bot_app signals: {str(e)}")
