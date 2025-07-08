from django.db import models
from django.contrib.postgres.fields import ArrayField
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from PIL import Image
from django.core.exceptions import ValidationError
import os


class InvitedUser(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255)
    role = ArrayField(models.CharField(max_length=100), blank=True, default=list)
    last_login = models.DateTimeField(null=True, blank=True)  # Allow null values
    email = models.EmailField(unique=True)
    is_administrator = models.BooleanField(default=False)
    status = models.CharField(max_length=50, default='activated', null=False, blank=True)  # ✅ Add default
    password = models.BinaryField()


    def __str__(self):
        return self.name
    
    class Meta:
        db_table = "invited_users"

class Installation(models.Model):
    id = models.AutoField(primary_key=True)  # Serial (Auto-increment)
    room = models.IntegerField(null=True, blank=True)
    product_available = models.TextField(null=True, blank=True)
    prework = models.TextField(null=True, blank=True)
    prework_checked_by = models.ForeignKey(
        InvitedUser, null=True, blank=True, on_delete=models.SET_NULL, related_name='prework_checked_by'
    )
    prework_check_on = models.DateTimeField(null=True, blank=True)

    install = models.TextField(null=True, blank=True)
    post_work = models.TextField(null=True, blank=True)
    post_work_checked_by = models.ForeignKey(
        InvitedUser, null=True, blank=True, on_delete=models.SET_NULL, related_name='post_work_checked_by'
    )
    post_work_check_on = models.DateTimeField(null=True, blank=True)
    day_install_began = models.DateTimeField(null=True, blank=True)
    day_install_complete = models.DateTimeField(null=True, blank=True)
    product_arrived_at_floor= models.TextField(null=True, blank=True)
    product_arrived_at_floor_checked_by = models.ForeignKey(
        InvitedUser, null=True, blank=True, on_delete=models.SET_NULL, related_name='product_arrived_checked_by'
    )
    product_arrived_at_floor_check_on = models.DateTimeField(null=True, blank=True)

    retouching= models.TextField(null=True, blank=True)
    retouching_checked_by = models.ForeignKey(
        InvitedUser, null=True, blank=True, on_delete=models.SET_NULL, related_name='retouching_checked_by'
    )
    retouching_check_on = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'install'  # Ensure this matches the actual table name in PostgreSQL

    def __str__(self):
        return f"Room {self.room} - Installation Status"

class Inventory(models.Model):
    id = models.AutoField(primary_key=True)  # Serial (Auto-increment)
    item = models.TextField(null=True, blank=True)
    client_id = models.TextField(null=True, blank=True)
    qty_ordered = models.IntegerField(null=True, blank=True)
    quantity_shipped = models.IntegerField(null=True, blank=True, default=0)
    qty_received = models.IntegerField(null=True, blank=True)
    damaged_quantity = models.IntegerField(null=True, blank=True, default=0)
    quantity_available = models.IntegerField(null=True, blank=True)
    shipped_to_hotel_quantity = models.IntegerField(null=True, blank=True)
    received_at_hotel_quantity = models.IntegerField(null=True, blank=True)
    damaged_quantity_at_hotel = models.IntegerField(null=True, blank=True)
    hotel_warehouse_quantity = models.IntegerField(null=True, blank=True, default=0)
    floor_quantity = models.IntegerField(null=True, blank=True, default=0)
    quantity_installed = models.IntegerField(null=True, blank=True)
    class Meta:
        db_table = 'inventory'  # Ensure this matches the actual table name in PostgreSQL

    def __str__(self):
        return f"Item: {self.item} - Available: {self.quantity_available}"
    
class HotelWarehouse(models.Model):
    id = models.AutoField(primary_key=True)
    reference_id = models.CharField(max_length=255)  # Warehouse Container ID 
    client_item = models.CharField(max_length=255)
    quantity_received = models.PositiveIntegerField(default=0)
    damaged_qty = models.PositiveIntegerField(default=0)
    checked_by = models.ForeignKey('InvitedUser', on_delete=models.SET_NULL, null=True, blank=True)
    received_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"{self.reference_id} - {self.client_item} ({self.quantity_received})"

    class Meta:
        db_table = "hotel_warehouse"

class WarehouseRequest(models.Model):
    id = models.AutoField(primary_key=True)
    floor_number = models.IntegerField()
    client_item = models.CharField(max_length=255)
    requested_by = models.ForeignKey('InvitedUser', on_delete=models.SET_NULL, null=True, blank=True)
    received_by = models.ForeignKey('InvitedUser', null=True, blank=True, on_delete=models.SET_NULL , related_name='received_requests')
    sent_by = models.ForeignKey('InvitedUser', null=True, blank=True, on_delete=models.SET_NULL, related_name='sent_requests')
    quantity_requested = models.PositiveIntegerField(default=0)
    quantity_received = models.PositiveIntegerField(default=0)
    quantity_sent = models.PositiveIntegerField(default=0)
    sent = models.BooleanField(default=False)  # True for Yes, False for No
    sent_date = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Floor {self.floor_number} - {self.client_item} (Requested: {self.quantity_requested}, Sent: {self.quantity_sent})"

    class Meta:
        db_table = "warehouse_request"
    
class RoomModel(models.Model):
    id = models.AutoField(primary_key=True)  # Serial (Auto-increment)
    room_model = models.TextField(null=True, blank=True)
    total = models.IntegerField(null=True, blank=True)

    class Meta:
        db_table = 'room_model'  # Ensure this matches the actual table name in PostgreSQL

    def __str__(self):
        return f"Room Model: {self.room_model} - Total: {self.total}"

class RoomData(models.Model):
    id = models.AutoField(primary_key=True)  # Serial (Auto-increment)
    room = models.IntegerField(null=True, blank=True)
    floor = models.IntegerField(null=True, blank=True)
    # king = models.TextField(null=True, blank=True)
    # double = models.TextField(null=True, blank=True)
    # exec_king = models.TextField(null=True, blank=True)
    bath_screen = models.TextField(null=True, blank=True)
    room_model = models.TextField(null=True, blank=True)
    room_model_id = models.ForeignKey(RoomModel, on_delete=models.SET_NULL, null=True, blank=True,db_column='room_model_id')
    left_desk = models.TextField(null=True, blank=True)
    right_desk = models.TextField(null=True, blank=True)
    to_be_renovated = models.TextField(null=True, blank=True)
    description = models.TextField(null=True, blank=True)  # Fixed column name typo from "descripton"
    bed = models.CharField(max_length=100, null=True, blank=True)  # Add this field if not present

    class Meta:
        db_table = 'room_data'  # Ensure this matches the actual table name in PostgreSQL

    def __str__(self):
        return f"Room {self.room} - Floor {self.floor}"
    
    def save(self, *args, **kwargs):
        # Store the previous room_model_id before save
        old_room_model_id = None
        if self.pk:
            old = RoomData.objects.filter(pk=self.pk).first()
            if old:
                old_room_model_id = old.room_model_id_id

        super().save(*args, **kwargs)

        # Update totals for both old and new room_model_ids
        if old_room_model_id and old_room_model_id != self.room_model_id_id:
            RoomModel.objects.filter(id=old_room_model_id).update(
                total=RoomData.objects.filter(room_model_id=old_room_model_id).count()
            )

        if self.room_model_id_id:
            RoomModel.objects.filter(id=self.room_model_id_id).update(
                total=RoomData.objects.filter(room_model_id=self.room_model_id_id).count()
            )

    def delete(self, *args, **kwargs):
        room_model_id = self.room_model_id_id
        super().delete(*args, **kwargs)
        if room_model_id:
            RoomModel.objects.filter(id=room_model_id).update(
                total=RoomData.objects.filter(room_model_id=room_model_id).count()
            )

class Schedule(models.Model):
    id = models.AutoField(primary_key=True)
    phase = models.IntegerField(null=True, blank=True)
    floor = models.IntegerField(null=True, blank=True)
    production_starts = models.DateField(null=True, blank=True)
    production_ends = models.DateField(null=True, blank=True)
    shipping_depature = models.DateField(null=True, blank=True)
    shipping_arrival = models.DateField(null=True, blank=True)
    custom_clearing_starts = models.DateField(null=True, blank=True)
    custom_clearing_ends = models.DateField(null=True, blank=True)
    arrive_on_site = models.DateField(null=True, blank=True)
    pre_work_starts = models.DateField(null=True, blank=True)
    pre_work_ends = models.DateField(null=True, blank=True)
    install_starts = models.DateField(null=True, blank=True)
    install_ends = models.DateField(null=True, blank=True)  # Keep as DateField
    
    post_work_starts = models.DateField(null=True, blank=True)  # Changed to DateField
    post_work_ends = models.DateField(null=True, blank=True)  # Changed to DateField
    floor_completed = models.DateField(null=True, blank=True)  # Changed to DateField
    floor_closes = models.DateField(null=True, blank=True)  # Changed to DateField
    floor_opens = models.DateField(null=True, blank=True)  # Changed to DateField


    class Meta:
        db_table = 'schedule'  # Ensures it maps to the existing table

    def __str__(self):
        return f"Schedule - Phase: {self.phase}, Floor: {self.floor}"

class ProductData(models.Model):
    id = models.AutoField(primary_key=True)
    item = models.TextField(null=True, blank=True)
    client_id = models.TextField(null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    client_selected = models.TextField(null=True, blank=True)
    supplier = models.TextField(null=True, blank=True)
    image = models.ImageField(upload_to='product_images/', null=True, blank=True)

    class Meta:
        db_table = 'product_data'  # Ensures it maps to the correct table

    def __str__(self):
        return f"ProductData - Item: {self.item}, Client: {self.client_id}"
    def save(self, *args, **kwargs):
        # Check format
        if self.image:
            ext = os.path.splitext(self.image.name)[1].lower()
            if ext not in ['.jpg', '.jpeg', '.png', '.webp']:
                raise ValidationError(f'Unsupported file format: {ext}')

        super().save(*args, **kwargs)  # Save first to ensure file exists

        # Resize image
        if self.image:
            img = Image.open(self.image.path)
            if img.height > 1200 or img.width > 1200:
                img.thumbnail((1200, 1200))
                img.save(self.image.path)    

class ChatSession(models.Model):
    user = models.ForeignKey(InvitedUser, on_delete=models.CASCADE)
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    topic = models.TextField(null=True, blank=True)
    metadata = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = 'chat_sessions'

    def __str__(self):
        return f"Session {self.id} by {self.user}"

# 🔷 3. Chat Messages
class ChatMessage(models.Model):
    ROLE_CHOICES = [
        ('human', 'Human'),
        ('ai', 'AI'),
    ]
    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE)
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    content = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)
    response_latency = models.FloatField(null=True, blank=True)
    token_count = models.IntegerField(null=True, blank=True)
    metadata = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = 'chat_messages'

    def __str__(self):
        return f"{self.role.upper()} @ {self.timestamp}"

# 🔷 4. Chat Memory (Optional)
class ChatMemory(models.Model):
    user = models.ForeignKey(InvitedUser, on_delete=models.CASCADE)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    source_session = models.ForeignKey(ChatSession, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        db_table = 'chat_memory'

    def __str__(self):
        return f"Memory of {self.user}"

# 🔷 5. Chat Prompts
class ChatPrompt(models.Model):
    name = models.CharField(max_length=100)
    instruction = models.TextField()
    examples = models.JSONField(null=True, blank=True)
    validations = models.TextField(null=True, blank=True)
    created_by = models.ForeignKey(InvitedUser, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'chat_prompts'

    def __str__(self):
        return self.name

# 🔷 6. Chat Evaluations
class ChatEvaluation(models.Model):
    test_case = models.TextField()
    expected_output = models.TextField()
    actual_output = models.TextField()
    passed = models.BooleanField()
    score = models.FloatField(null=True, blank=True)
    latency = models.FloatField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'chat_evaluations'

    def __str__(self):
        return f"Eval {self.id}: {'✅' if self.passed else '❌'}"

class Shipping(models.Model):
    client_id = models.CharField(max_length=255)
    item = models.CharField(max_length=255)
    ship_date = models.DateField()
    ship_qty = models.PositiveIntegerField()
    supplier = models.CharField(max_length=255)
    bol = models.CharField("Bill of Lading", max_length=100, unique=True)
    checked_by = models.ForeignKey(InvitedUser, on_delete=models.SET_NULL, null=True, blank=True,db_column='checked_by')
    expected_arrival_date = models.DateTimeField(default=None,null=True, blank=True)

    def __str__(self):
        return f"Shipment {self.bol} - {self.item} to Client {self.client_id}"
    class Meta:
        db_table = "shipping"

class WarehouseShipment(models.Model):
    client_id = models.CharField(max_length=255)
    item = models.CharField(max_length=255)
    ship_date = models.DateField()
    ship_qty = models.PositiveIntegerField()
    reference_id = models.CharField(max_length=100)
    checked_by = models.ForeignKey(InvitedUser, on_delete=models.SET_NULL, null=True, blank=True)
    expected_arrival_date = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Warehouse Shipment {self.reference_id} - {self.item}"
    
    class Meta:
        db_table = "warehouse_shipment"

class PullInventory(models.Model):
    client_id =  models.CharField(max_length=255)
    item = models.CharField(max_length=255)
    available_qty = models.PositiveIntegerField(default=0)
    pulled_date = models.DateField(null=True, blank=True)
    qty_pulled = models.PositiveIntegerField(default=0)
    pulled_by = models.ForeignKey(InvitedUser, on_delete=models.SET_NULL, null=True, blank=True,db_column='checked_by')
    floor = models.CharField(max_length=100, blank=True)
    qty_available_after_pull = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"Pull - {self.item} for Client {self.client_id}"
    class Meta:
        db_table = "pull_inventory"

class InstallDetail(models.Model):
    install_id = models.AutoField(primary_key=True)
    installation = models.ForeignKey(Installation, on_delete=models.CASCADE, null=True, blank=True, related_name='install_details')
    product_id=models.ForeignKey(ProductData, on_delete=models.SET_NULL, null=True, blank=True,db_column='product_id')
    room_model_id=models.ForeignKey(RoomModel, on_delete=models.SET_NULL, null=True, blank=True,db_column='room_model_id')
    room_id = models.ForeignKey(RoomData, on_delete=models.SET_NULL, null=True, blank=True,db_column='room_id')
    product_name = models.CharField(max_length=255)
    installed_by = models.ForeignKey(InvitedUser, on_delete=models.SET_NULL, null=True, blank=True,db_column='installed_by')
    installed_on = models.DateTimeField(default=None,null=True, blank=True)
    status=models.TextField(default='NO')

    def __str__(self):
        return f"Install {self.install_id} - {self.product_name} in Room {self.room_id}"
    class Meta:
        db_table = "install_detail"

class ProductRoomModel(models.Model):
    id=models.AutoField(primary_key=True)
    product_id=models.ForeignKey(ProductData, on_delete=models.SET_NULL, null=True, blank=True,db_column='product_id')
    room_model_id=models.ForeignKey(RoomModel, on_delete=models.SET_NULL, null=True, blank=True,db_column='room_model_id')
    quantity=models.IntegerField(null=True)

    class Meta:
        db_table = "product_room_model"

class InventoryReceived(models.Model):
    client_id = models.CharField(max_length=255)
    item = models.CharField(max_length=255)
    received_date = models.DateTimeField()
    received_qty = models.PositiveIntegerField()
    damaged_qty = models.PositiveIntegerField()
    checked_by = models.ForeignKey(InvitedUser, on_delete=models.SET_NULL, null=True, blank=True)
    container_id = models.CharField(max_length=255, null=True, blank=True)

    def __str__(self):
        return f"Received {self.received_qty} of {self.item} for {self.client_id}"

    class Meta:
        db_table = "inventory_received"

class IssueStatus(models.TextChoices):
    OPEN = 'OPEN', _('Open')
    CLOSE = 'CLOSE', _('Close')
    PENDING = 'PENDING', _('Pending')
    WORKING = 'WORKING', _('Working')

class IssueType(models.TextChoices):
    ROOM = 'ROOM', _('Room')
    FLOOR = 'FLOOR', _('Floor')
    PRODUCT = 'PRODUCT', _('Product')
    OTHER = 'OTHER', _('Other')


class Issue(models.Model):
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        InvitedUser,
        on_delete=models.PROTECT,
        related_name='created_issues'
    )
    status = models.CharField(
        max_length=10,
        choices=IssueStatus.choices,
        default=IssueStatus.OPEN
    )
    type = models.CharField(
        max_length=10,
        choices=IssueType.choices
        # Default type might be better set in the view based on context
    )
    is_for_hotel_owner = models.BooleanField(default=False)
    assignee = models.ForeignKey(
        InvitedUser,
        on_delete=models.SET_NULL,
        related_name='assigned_issues',
        blank=True,
        null=True
    )
    observers = models.ManyToManyField(
        InvitedUser,
        related_name='observed_issues',
        blank=True
    )

    # New fields for linking to Rooms or Inventory based on IssueType
    related_rooms = models.ManyToManyField(
        RoomData,
        related_name='issues',
        blank=True,
        verbose_name='Related Rooms'
    )
    related_floors = ArrayField(
        models.IntegerField(),
        blank=True,
        null=True,
        help_text="List of floor numbers"
    )
    # related_floor = models.CharField(max_length=100)  # Example field
    related_product = models.ManyToManyField(
        ProductData,
        related_name='issues',
        blank=True,
        verbose_name='Related Product Items'
    )
    other_type_details = models.TextField(
        blank=True,
        null=True,
        verbose_name='Other Details'
    )
    # class Meta:
    #     db_table = 'hotel_bot_app_issue'
    #     ordering = ['-created_at']

    def __str__(self):
        return f"{self.id}: {self.title} ({self.status})"

class Comment(models.Model):
    issue = models.ForeignKey(
        Issue,
        on_delete=models.CASCADE, # Delete comments if the issue is deleted
        related_name='comments'
    )

    # Fields for GenericForeignKey to allow different commenter types
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,

    )
    object_id = models.PositiveIntegerField(
        # Same as above, potentially nullable during transition
    )
    commenter = GenericForeignKey('content_type', 'object_id')

    text_content = models.TextField(blank=True, null=True)
    # Using JSONField for flexibility. Requires PostgreSQL or Django >= 3.1 with other DBs
    # Consider alternatives like a separate Media model if JSONField is not suitable
    media = models.JSONField(default=list, blank=True) # Stores list of media info
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def commenter_display_name(self):
        if not self.commenter:
            return "Unknown User"


        if isinstance(self.commenter, InvitedUser):
            return self.commenter.name
        # Check if it's an instance of the model specified by AUTH_USER_MODEL
        # This requires from django.conf import settings
        elif isinstance(self.commenter, eval(settings.AUTH_USER_MODEL.split('.')[0] + ".models." + settings.AUTH_USER_MODEL.split('.')[1])):
             # if settings.AUTH_USER_MODEL is 'auth.User' this becomes isinstance(self.commenter, auth.models.User)
            user_model_name = self.commenter.__class__.__name__
            if hasattr(self.commenter, 'get_full_name') and self.commenter.get_full_name():
                return self.commenter.get_full_name()
            elif hasattr(self.commenter, 'username'):
                return self.commenter.username
            else: # Fallback for other potential user models
                return str(self.commenter)

        return str(self.commenter) # Fallback

    def __str__(self):
        return f"Comment by {self.commenter_display_name} on {self.issue.title} at {self.created_at.strftime('%Y-%m-%d %H:%M')}"

    class Meta:
        ordering = ['created_at']

# Potential next steps:
# - Add validation for media field (max images, video size) - likely in forms/serializers
# - Create a signal to automatically add creator to observers on Issue save

"""
from hotel_bot_app.models import Issue, IssueStatus, IssueType, InvitedUser
from django.utils import timezone
inv = InvitedUser.objects.filter(id=7).first()
issue2 = Issue.objects.create(

    title="Broken Tile on 3rd Floor Hallway",

    description="A tile in the hallway on the 3rd floor is cracked and loose. Potential tripping hazard.",

    created_by=inv,

    status=IssueStatus.WORKING,

    type=IssueType.FLOOR,

    assignee=None,

    is_for_hotel_admin=True,

    created_at=timezone.now() - timezone.timedelta(days=1) # Example of setting a past creation date

)
"""

# New UserProfile model for Django User
class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    is_administrator = models.BooleanField(default=False)
    
    def __str__(self):
        return f"{self.user.username}'s profile"

# Signal to create UserProfile when User is created
@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)

@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    instance.profile.save()