from django.contrib import admin
from .models import Issue, Comment, InvitedUser, Inventory, InventoryReceived, UserProfile # Assuming other models are already imported

# Basic registration first, can customize later
admin.site.register(Issue)
admin.site.register(Comment)

# Keep existing registrations
admin.site.register(InvitedUser)
admin.site.register(Inventory)
admin.site.register(InventoryReceived)

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'is_administrator']
    list_filter = ['is_administrator']
    search_fields = ['user__username', 'user__email']
