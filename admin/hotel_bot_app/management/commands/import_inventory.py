import csv
from django.core.management.base import BaseCommand
from hotel_bot_app.models import Inventory

class Command(BaseCommand):
    help = 'Import inventory data from a CSV file'

    def add_arguments(self, parser):
        parser.add_argument('csv_file', type=str, help='Path to the CSV file')

    def handle(self, *args, **kwargs):
        csv_file = kwargs['csv_file']
        with open(csv_file, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Convert empty strings to None or 0 as appropriate
                def to_int(val):
                    return int(val) if val and val.strip() != '' else 0
                inventory, created = Inventory.objects.update_or_create(
                    id=to_int(row['id']),
                    defaults={
                        'item': row['item'],
                        'client_id': row['client_id'],
                        'qty_ordered': to_int(row['qty_ordered']),
                        'qty_received': to_int(row['qty_received']),
                        'quantity_installed': to_int(row['quantity_installed']),
                        'quantity_available': to_int(row['quantity_available']),
                        'hotel_warehouse_quantity': to_int(row['hotel_warehouse_quantity']),
                        'quantity_shipped': to_int(row['quantity_shipped']),
                        'floor_quantity': to_int(row['floor_quantity']),
                        'damaged_quantity': to_int(row['damaged_quantity']),
                        'damaged_quantity_at_hotel': to_int(row['damaged_quantity_at_hotel']),
                        'received_at_hotel_quantity': to_int(row['received_at_hotel_quantity']),
                        'shipped_to_hotel_quantity': to_int(row['shipped_to_hotel_quantity']),
                    }
                )
                action = "Created" if created else "Updated"
                self.stdout.write(f"{action} Inventory: {inventory.item} (ID: {inventory.id})")
        self.stdout.write(self.style.SUCCESS('Inventory import completed.'))
