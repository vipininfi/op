from django import template

register = template.Library()

@register.filter(name="underscore_to_space")
def underscore_to_space(value):
    if isinstance(value, str):
        return value.replace("_", " ")
    return value
