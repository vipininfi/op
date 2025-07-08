import os
import django
import sys

# Set up Django environment
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'admin.settings')
django.setup()

from django.contrib.auth.models import User

def remove_superuser_status():
    try:
        user = User.objects.get(username='new2')
        print(f"Before update: {user.username} is_superuser={user.is_superuser}")
        
        # Remove superuser status
        user.is_superuser = False
        user.save()
        
        print(f"After update: {user.username} is_superuser={user.is_superuser}")
        print("Superuser status successfully removed.")
    except User.DoesNotExist:
        print("User 'new2' not found")

if __name__ == "__main__":
    remove_superuser_status() 