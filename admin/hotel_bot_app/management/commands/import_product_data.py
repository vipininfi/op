import csv
from django.core.management.base import BaseCommand
from hotel_bot_app.models import ProductData

class Command(BaseCommand):
    help = 'Import product data from a CSV file into ProductData table'

    def add_arguments(self, parser):
        parser.add_argument('csv_file', type=str, help='Path to the CSV file')

    def handle(self, *args, **kwargs):
        csv_file = kwargs['csv_file']
        with open(csv_file, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                product, created = ProductData.objects.update_or_create(
                    id=int(row['id']),
                    defaults={
                        'item': row['item'],
                        'client_id': row['client_id'],
                        'description': row['description'],
                        'client_selected': row['client_selected'],
                        'supplier': row['supplier'],
                        'image': row['image'] if row['image'] else None,
                    }
                )
                action = "Created" if created else "Updated"
                self.stdout.write(f"{action} ProductData: {product.item} (ID: {product.id})")
        self.stdout.write(self.style.SUCCESS('ProductData import completed.'))
