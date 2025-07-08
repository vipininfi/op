from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.db.models import Sum
from .models import Shipping, Inventory, WarehouseRequest
import logging

logger = logging.getLogger(__name__)

@receiver([post_save, post_delete], sender=Shipping)
def update_inventory_shipped_quantity(sender, instance, **kwargs):
    """
    Update the quantity_shipped in Inventory table whenever a Shipping record is added, updated, or deleted
    """
    try:
        print(f"Signal triggered for Shipping record: client_id={instance.client_id}, ship_qty={instance.ship_qty}")
        
        # Get all shipping records for this client_id (case-insensitive)
        shipping_records = Shipping.objects.filter(client_id__iexact=instance.client_id)
        total_shipped = shipping_records.aggregate(total=Sum('ship_qty'))['total'] or 0
        
        print(f"Found {shipping_records.count()} shipping records for client_id={instance.client_id} (case-insensitive)")
        print(f"Total shipped quantity: {total_shipped}")
        
        # First try to find existing inventory record case-insensitively
        try:
            inventory = Inventory.objects.get(client_id__iexact=instance.client_id)
            print(f"Found existing inventory record for client_id={instance.client_id}")
            inventory.quantity_shipped = total_shipped
            inventory.save()
        except Inventory.DoesNotExist:
            # If no record exists, create a new one
            print(f"Creating new inventory record for client_id={instance.client_id}")
            inventory = Inventory.objects.create(
                client_id=instance.client_id,
                item=instance.client_id,
                quantity_shipped=total_shipped
            )

        print(f"Successfully updated quantity_shipped to {total_shipped} for client_id={instance.client_id}")
            
    except Exception as e:
        print(f"Error updating inventory shipped quantity: {str(e)}")
        logger.error(f"Error updating inventory shipped quantity: {str(e)}", exc_info=True)

@receiver([post_save, post_delete], sender=WarehouseRequest)
def update_inventory_floor_quantity(sender, instance, **kwargs):
    """
    Update the floor_quantity in Inventory table whenever a WarehouseRequest record is added, updated, or deleted
    """
    try:
        print(f"Signal triggered for WarehouseRequest record: client_item={instance.client_item}")

        # Get all warehouse request records for this client_item (case-insensitive)
        warehouse_records = WarehouseRequest.objects.filter(client_item__iexact=instance.client_item)
        total_sent = warehouse_records.aggregate(total=Sum('quantity_sent'))['total'] or 0

        print(f"Found {warehouse_records.count()} warehouse request records for client_item={instance.client_item}")
        print(f"Total quantity sent: {total_sent}")

        # First try to find existing inventory record case-insensitively
        try:
            inventory = Inventory.objects.get(client_id__iexact=instance.client_item)
            print(f"Found existing inventory record for client_id={instance.client_item}")
            inventory.floor_quantity = total_sent
            inventory.save()
        except Inventory.DoesNotExist:
            # If no record exists, create a new one
            print(f"Creating new inventory record for client_id={instance.client_item}")
            inventory = Inventory.objects.create(
                client_id=instance.client_item,
                item=instance.client_item,
                floor_quantity=total_sent
            )

        print(f"Successfully updated floor_quantity to {total_sent} for client_id={instance.client_item}")

    except Exception as e:
        print(f"Error updating inventory floor quantity: {str(e)}")
        logger.error(f"Error updating inventory floor quantity: {str(e)}", exc_info=True) 