from django import template

register = template.Library()

@register.filter
def is_superuser_only(user):
    """
    Check if the user is a superuser (not just administrator)
    """
    return user.is_superuser

@register.filter
def is_admin_or_superuser(user):
    """
    Check if the user is either a superuser or has is_administrator=True
    """
    if user.is_superuser:
        return True
        
    if hasattr(user, 'profile') and user.profile.is_administrator:
        return True
        
    return False 