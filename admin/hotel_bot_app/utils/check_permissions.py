import os
import django
import sys

# Set up Django environment
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'admin.settings')
django.setup()

from django.contrib.auth.models import User
from hotel_bot_app.models import UserProfile, InvitedUser

def check_all_users():
    print("=== Django Users ===")
    for user in User.objects.all():
        has_profile = hasattr(user, 'profile')
        is_administrator = False
        if has_profile:
            is_administrator = user.profile.is_administrator
        
        print(f"Username: {user.username}")
        print(f"Email: {user.email}")
        print(f"Is superuser: {user.is_superuser}")
        print(f"Is staff: {user.is_staff}")
        print(f"Has profile: {has_profile}")
        print(f"Is administrator: {is_administrator}")
        print("-" * 40)
    
    print("\n=== InvitedUsers ===")
    for invited_user in InvitedUser.objects.all():
        print(f"Name: {invited_user.name}")
        print(f"Email: {invited_user.email}")
        print(f"Role: {invited_user.role}")
        print(f"Is administrator: {invited_user.is_administrator}")
        print("-" * 40)

if __name__ == "__main__":
    check_all_users() 