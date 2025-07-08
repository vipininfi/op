from django import template

register = template.Library()

PALETTE = [
    "#6e7f80",
    "#536872",
    "#708090",
    "#536878",
    "#36454f",
]

@register.filter
def bubble_color(user_id):
    try:
        idx = int(user_id) % len(PALETTE)
        return PALETTE[idx]
    except Exception:
        return PALETTE[0] 