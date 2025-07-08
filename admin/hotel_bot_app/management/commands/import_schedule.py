import csv
from django.core.management.base import BaseCommand
from hotel_bot_app.models import Schedule
from datetime import datetime

class Command(BaseCommand):
    help = 'Import schedule data from a CSV file into Schedule table'

    def add_arguments(self, parser):
        parser.add_argument('csv_file', type=str, help='Path to the CSV file')

    def parse_date(self, value):
        if not value or value.strip() == '':
            return None
        # Remove timezone if present
        value = value.split('+')[0].strip()
        # Try parsing with time
        try:
            return datetime.strptime(value, '%Y-%m-%d %H:%M:%S.%f')
        except ValueError:
            try:
                return datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                try:
                    return datetime.strptime(value, '%Y-%m-%d')
                except ValueError:
                    return None

    def handle(self, *args, **kwargs):
        csv_file = kwargs['csv_file']
        with open(csv_file, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                schedule, created = Schedule.objects.update_or_create(
                    id=int(row['id']),
                    defaults={
                        'phase': int(row['phase']) if row['phase'] else None,
                        'floor': int(row['floor']) if row['floor'] else None,
                        'production_starts': self.parse_date(row['production_starts']),
                        'production_ends': self.parse_date(row['production_ends']),
                        'shipping_depature': self.parse_date(row['shipping_depature']),
                        'shipping_arrival': self.parse_date(row['shipping_arrival']),
                        'custom_clearing_starts': self.parse_date(row['custom_clearing_starts']),
                        'custom_clearing_ends': self.parse_date(row['custom_clearing_ends']),
                        'arrive_on_site': self.parse_date(row['arrive_on_site']),
                        'pre_work_starts': self.parse_date(row['pre_work_starts']),
                        'pre_work_ends': self.parse_date(row['pre_work_ends']),
                        'install_starts': self.parse_date(row['install_starts']),
                        'install_ends': self.parse_date(row['install_ends']),
                        'post_work_starts': self.parse_date(row['post_work_starts']),
                        'post_work_ends': self.parse_date(row['post_work_ends']),
                        'floor_completed': self.parse_date(row['floor_completed']),
                        'floor_closes': self.parse_date(row['floor_closes']),
                        'floor_opens': self.parse_date(row['floor_opens']),
                    }
                )
                action = "Created" if created else "Updated"
                self.stdout.write(f"{action} Schedule: {schedule.id} (Phase: {schedule.phase}, Floor: {schedule.floor})")
        self.stdout.write(self.style.SUCCESS('Schedule import completed.'))
