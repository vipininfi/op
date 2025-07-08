import logging
from datetime import datetime
from django.db import connection

logger = logging.getLogger(__name__)

class DatabaseExecutor:
    """Executes SQL queries and returns formatted results"""
    
    def execute_query(self, sql_query: str) -> dict:
        """Execute SQL query and return formatted results"""
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql_query)
                columns = [col[0] for col in cursor.description]
                rows = cursor.fetchall()
                results = []
                for row in rows:
                    result_dict = {}
                    for i, value in enumerate(row):
                        if isinstance(value, datetime):
                            value = value.isoformat()
                        result_dict[columns[i]] = value
                    results.append(result_dict)
                return {
                    'success': True,
                    'columns': columns,
                    'results': results,
                    'count': len(results)
                }
        except Exception as e:
            logger.error(f"Database execution error: {e}")
            return {
                'success': False,
                'error': str(e),
                'columns': [],
                'results': [],
                'count': 0
            }
    
    def format_results_as_table(self, db_result: dict) -> str:
        """Format database results as markdown table"""
        if not db_result['success']:
            return f"**Database Error:** {db_result['error']}"
        if not db_result['results']:
            return "**Database Results:** No data found for your query."
        formatted = "**Database Results:**\n\n"
        formatted += "| " + " | ".join(db_result['columns']) + " |\n"
        formatted += "| " + " | ".join(["---"] * len(db_result['columns'])) + " |\n"
        for row in db_result['results']:
            formatted += "| " + " | ".join([str(v) for v in row.values()]) + " |\n"
        formatted += f"\n**Total Records:** {db_result['count']}"
        return formatted
