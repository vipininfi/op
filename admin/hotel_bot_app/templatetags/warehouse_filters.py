from django import template

register = template.Library()

@register.filter
def all_items_sent(items_list):
    """
    Check if all items in a list of warehouse requests have been sent
    Returns True if all items are sent, False otherwise
    """
    if not items_list:
        return True
    
    for item in items_list:
        if not item.sent:
            return False
    
    return True 