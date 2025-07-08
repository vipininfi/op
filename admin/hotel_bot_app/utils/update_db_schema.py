import os
import django
import yaml
from django.db import connection
from collections import OrderedDict
from datetime import datetime, date

# Setup Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "admin.settings")
django.setup()

def serialize_value(val):
    if isinstance(val, (datetime, date)):
        return val.isoformat()
    return val

def clean_row(row):
    return {k: serialize_value(v) for k, v in row.items()}

def load_and_enrich_yaml_with_examples():
    base_dir = os.path.dirname(__file__)
    yaml_path = os.path.join(base_dir, "db_schema.yaml")

    # Load schema YAML
    with open(yaml_path, "r") as f:
        schema_data = yaml.safe_load(f)

    enriched = False

    for table in schema_data.get("tables", []):
        table_name = table["name"]

        if "example" in table:
            continue

        query = f'SELECT * FROM "{table_name}" LIMIT 2;'

        try:
            with connection.cursor() as cursor:
                cursor.execute(query)
                columns = [col[0] for col in cursor.description]
                rows = cursor.fetchall()

            example_rows = [OrderedDict(zip(columns, row)) for row in rows]
            cleaned_rows = [clean_row(dict(row)) for row in example_rows]

            if cleaned_rows:
                table["example"] = cleaned_rows
                enriched = True

        except Exception as e:
            print(f"⚠️ Failed to fetch data for table '{table_name}': {e}")

    if enriched:
        with open(yaml_path, "w") as f:
            yaml.dump(schema_data, f, sort_keys=False, default_flow_style=False)
        print("✅ YAML updated with examples.")
    else:
        print("ℹ️ No new examples added. YAML is already up to date.")

    return schema_data

# Run it
if __name__ == "__main__":
    output = load_and_enrich_yaml_with_examples()
    print(output)
