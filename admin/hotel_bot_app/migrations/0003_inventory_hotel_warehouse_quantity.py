# Generated by Django 5.2 on 2025-06-12 10:01

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hotel_bot_app', '0002_inventoryreceived_container_id_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='inventory',
            name='hotel_warehouse_quantity',
            field=models.IntegerField(blank=True, default=0, null=True),
        ),
    ]
