
import csv
import os
from django.core.management.base import BaseCommand
from hotel_bot_app.core.chatbot_core import InventoryChatbot

class Command(BaseCommand):
    help = 'Evaluate chatbot on questions from a CSV and save responses to a new CSV or update the same file.'


    # Hardcoded file paths (edit as needed)
    INPUT_CSV_PATH = r"C:/Users/HP/Desktop/new1/hotel-installation-bot/chatbot_evaluation_questions.csv"
    OUTPUT_CSV_PATH = INPUT_CSV_PATH  # or set a different path if you want a separate output

    def handle(self, *args, **options):
        input_path = self.INPUT_CSV_PATH
        output_path = self.OUTPUT_CSV_PATH

        if not os.path.exists(input_path):
            self.stdout.write(self.style.ERROR(f'Input file not found: {input_path}'))
            return

        with open(input_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            fieldnames = reader.fieldnames

        if 'Question' not in fieldnames or 'ChatbotResponse' not in fieldnames:
            self.stdout.write(self.style.ERROR('CSV must have columns: Question, ChatbotResponse'))
            return

        chatbot = InventoryChatbot()
        session = chatbot.create_session("Eval User")

        for i, row in enumerate(rows, 1):
            question = row['Question']
            if not question.strip():
                continue
            self.stdout.write(f'[{i}/{len(rows)}] Q: {question}')
            try:
                response = ""
                if hasattr(chatbot, 'process_question'):
                    for chunk in chatbot.process_question(question, session.id):
                        response += chunk
                elif hasattr(chatbot, 'process_question_streaming'):
                    for event in chatbot.process_question_streaming(question, session.id):
                        if event.get('type') == 'final':
                            response += event.get('content', '')
                else:
                    response = "[No process_question method found]"
                row['ChatbotResponse'] = response
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'Error: {e}'))
                row['ChatbotResponse'] = f'Error: {e}'

        # Write updated rows to output CSV
        with open(output_path, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

        self.stdout.write(self.style.SUCCESS(f'Results written to {output_path}'))