import os
import django
import sys

# Set up Django environment
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'admin.settings')
django.setup()

from django.contrib.auth.models import User
from hotel_bot_app.models import UserProfile

def create_missing_profiles():
    users_without_profiles = []
    profiles_created = 0
    
    for user in User.objects.all():
        try:
            # Try to access profile
            profile = user.profile
            print(f"User {user.username} already has a profile")
        except User.profile.RelatedObjectDoesNotExist:
            # Create profile if it doesn't exist
            users_without_profiles.append(user)
            UserProfile.objects.create(user=user)
            profiles_created += 1
            print(f"Created profile for user: {user.username}")
    
    print(f"\nCreated {profiles_created} profiles for users without profiles")
    print(f"Users processed: {User.objects.count()}")
    
    if not users_without_profiles:
        print("All users already had profiles")
    
if __name__ == "__main__":
    create_missing_profiles() 