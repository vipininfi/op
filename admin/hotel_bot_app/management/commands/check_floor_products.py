from django.core.management.base import BaseCommand
from hotel_bot_app.models import RoomData, RoomModel, ProductRoomModel, ProductData

class Command(BaseCommand):
    help = 'Check floor-to-product mapping for a given floor number.'

    def add_arguments(self, parser):
        parser.add_argument('floor_number', type=int, help='Floor number to check')

    def handle(self, *args, **kwargs):
        floor = kwargs['floor_number']
        rooms = RoomData.objects.filter(floor=floor)
        if not rooms.exists():
            self.stdout.write(self.style.ERROR(f'No rooms found for floor {floor}'))
            return
        self.stdout.write(self.style.SUCCESS(f'Rooms on floor {floor}: {[r.room for r in rooms]}'))
        for room in rooms:
            if not room.room_model_id:
                self.stdout.write(self.style.WARNING(f'Room {room.room} has no room_model_id'))
                continue
            model = room.room_model_id
            self.stdout.write(f'Room {room.room} uses model: {model.id} ({model.room_model})')
            mappings = ProductRoomModel.objects.filter(room_model_id=model)
            if not mappings.exists():
                self.stdout.write(self.style.WARNING(f'  No ProductRoomModel mapping for model {model.id}'))
                continue
            for mapping in mappings:
                product = mapping.product_id
                if not product:
                    self.stdout.write(self.style.WARNING(f'    Mapping {mapping.id} has no product_id'))
                    continue
                self.stdout.write(self.style.SUCCESS(f'    Product: {product.id} | {product.client_id} | {product.description}'))
