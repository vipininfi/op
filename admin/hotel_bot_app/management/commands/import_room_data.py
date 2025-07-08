import csv
from django.core.management.base import BaseCommand
from hotel_bot_app.models import RoomData, RoomModel

class Command(BaseCommand):
    help = 'Import room data from a CSV file into RoomData table'

    def add_arguments(self, parser):
        parser.add_argument('csv_file', type=str, help='Path to the CSV file')

    def handle(self, *args, **kwargs):
        csv_file = kwargs['csv_file']
        with open(csv_file, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                room_model_id = int(row['room_model_id']) if row['room_model_id'] else None
                room_model = RoomModel.objects.filter(id=room_model_id).first() if room_model_id else None
                room_data, created = RoomData.objects.update_or_create(
                    id=int(row['id']),
                    defaults={
                        'room': int(row['room']) if row['room'] else None,
                        'floor': int(row['floor']) if row['floor'] else None,
                        'bath_screen': row['bath_screen'],
                        'room_model': row['room_model'],
                        'left_desk': row['left_desk'],
                        'right_desk': row['right_desk'],
                        'to_be_renovated': row['to_be_renovated'],
                        'description': row['description'],
                        'room_model_id': room_model,
                        'bed': row['bed'],
                    }
                )
                action = "Created" if created else "Updated"
                self.stdout.write(f"{action} RoomData: {room_data.id} (Room: {room_data.room}, Floor: {room_data.floor})")
        self.stdout.write(self.style.SUCCESS('RoomData import completed.'))
