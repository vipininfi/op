import csv
from django.core.management.base import BaseCommand
from hotel_bot_app.models import ProductRoomModel, ProductData, RoomModel

class Command(BaseCommand):
    help = 'Import product-room-model data from a CSV file into ProductRoomModel table'

    def add_arguments(self, parser):
        parser.add_argument('csv_file', type=str, help='Path to the CSV file')

    def handle(self, *args, **kwargs):
        csv_file = kwargs['csv_file']
        with open(csv_file, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                product_id = int(row['product_id']) if row['product_id'] else None
                room_model_id = int(row['room_model_id']) if row['room_model_id'] else None
                product = ProductData.objects.filter(id=product_id).first() if product_id else None
                room_model = RoomModel.objects.filter(id=room_model_id).first() if room_model_id else None
                if not product or not room_model:
                    self.stdout.write(self.style.WARNING(f"Skipping row with product_id={product_id}, room_model_id={room_model_id} (missing FK)") )
                    continue
                prm, created = ProductRoomModel.objects.update_or_create(
                    id=int(row['id']),
                    defaults={
                        'product_id': product,
                        'room_model_id': room_model,
                        'quantity': int(row['quantity']) if row['quantity'] else None,
                    }
                )
                action = "Created" if created else "Updated"
                self.stdout.write(f"{action} ProductRoomModel: {prm.id} (Product: {product_id}, RoomModel: {room_model_id})")
        self.stdout.write(self.style.SUCCESS('ProductRoomModel import completed.'))
