import csv
from django.core.management.base import BaseCommand
from hotel_bot_app.models import RoomModel

class Command(BaseCommand):
    help = 'Import room model data from a CSV file into RoomModel table'

    def add_arguments(self, parser):
        parser.add_argument('csv_file', type=str, help='Path to the CSV file')

    def handle(self, *args, **kwargs):
        csv_file = kwargs['csv_file']
        with open(csv_file, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                room_model, created = RoomModel.objects.update_or_create(
                    id=int(row['id']),
                    defaults={
                        'room_model': row['room_model'],
                        'total': int(row['total']) if row['total'] else None,
                    }
                )
                action = "Created" if created else "Updated"
                self.stdout.write(f"{action} RoomModel: {room_model.id} ({room_model.room_model})")
        self.stdout.write(self.style.SUCCESS('RoomModel import completed.'))
