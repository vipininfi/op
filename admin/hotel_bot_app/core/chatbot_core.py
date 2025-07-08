import os
import re
import logging
import queue
from typing import Dict, List, Any, Generator
from datetime import datetime
from .database_executor import DatabaseExecutor
from django.conf import settings
from langchain_openai import ChatOpenAI
from langchain.agents import initialize_agent, Tool
from langchain.callbacks.base import BaseCallbackHandler
from langchain.memory import ConversationBufferMemory
from langchain.chains import LLMChain
from pydantic import BaseModel
from langchain_community.utilities import SQLDatabase 

from hotel_bot_app.models import InvitedUser, ChatSession, ChatMessage, ChatEvaluation
from .sql_query_generator import extract_sql_and_explanation
from .utils import save_to_chat_db, load_memory, get_session_history

# Use thread-local storage to keep queue per-thread (since each stream runs in a separate thread)
import threading
thread_local = threading.local()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)



# 1️⃣ Connect to your PostgreSQL DB
DATABASE_URI = f"postgresql+psycopg2://{settings.DATABASES['default']['USER']}:{settings.DATABASES['default']['PASSWORD']}@{settings.DATABASES['default']['HOST']}:{settings.DATABASES['default']['PORT']}/{settings.DATABASES['default']['NAME']}"
db = SQLDatabase.from_uri(DATABASE_URI)


# 2️⃣ LangChain LLM + Streaming Output
def build_llm(callbacks=None):
    return ChatOpenAI(
        model="gpt-4o",
        temperature=0.5,
        streaming=True,
        callbacks=callbacks or [],
        api_key=os.getenv("OPENAI_API_KEY"),
        verbose=False
    )

llm = build_llm()

# 3️⃣ Custom Prompt Template with Full Context
from .custom_prompt_template import custom_prompt


# 4️⃣ Tool: Chain using this prompt
chain = LLMChain(llm=llm, prompt=custom_prompt)


def get_chat_history_string(session_id, limit=10):
    """Return chat history as a formatted string for the prompt."""
    messages = list(get_session_history(session_id))
    history = []
    for msg in messages[-limit:]:
        role = 'User' if msg.role == 'human' else 'Assistant'
        history.append(f"{role}: {msg.content}")
    return '\n'.join(history)

def execute_sql_query(user_input):
    """Generate and execute SQL query for user input (chat history + question), streaming SQL and explanation if event_queue is provided"""
    try:
        event_queue = getattr(thread_local, "event_queue", None)
        # user_input is a string containing both chat history and question
        def flatten(text: str) -> str:
            return re.sub(r'[\r\n]+', ' ', text).strip()

        input_data = flatten(user_input)


        response = chain.run(input_data)
        sql_query, explanation = extract_sql_and_explanation(response)

        if event_queue and sql_query:
            event_queue.put({
                "type": "thinking",
                "content": f"<b> .</b><b>SQL Used:</b><br><pre style='white-space:pre-wrap;font-size:13px;background:#f8f9fa;border-radius:6px;padding:6px 8px;border:1px solid #e9ecef;'>{sql_query}</pre>"
            })
        if event_queue and explanation:
            event_queue.put({
                "type": "thinking",
                "content": f"<b>Explanation:</b> {explanation}"
            })
        if not sql_query:
            return "No SQL query generated from the question."
        forbidden = ["delete", "drop", "truncate", "update", "insert", "alter", "create"]
        if any(f in sql_query.lower() for f in forbidden):
            return "⚠️ Unsafe SQL query blocked."
        db_result = db_executor.execute_query(sql_query)
        if db_result['success']:
            if db_result['count'] == 0:
                final_response = "No data found for your query."
            elif db_result['count'] == 1:
                row = db_result['results'][0]
                if 'total_installed' in row:
                    final_response = f"There are {row['total_installed']} items installed."
                elif 'count' in row:
                    final_response = f"Found {row['count']} records."
                else:
                    final_response = "**Results:**\n\n"
                    for key, value in row.items():
                        final_response += f"**{key.replace('_', ' ').title()}:** {value}\n"
            else:
                final_response = f"Found {db_result['count']} records:\n\n"
                final_response += "| " + " | ".join(db_result['columns']) + " |\n"
                final_response += "| " + " | ".join(["---"] * len(db_result['columns'])) + " |\n"
                for row in db_result['results']:
                    final_response += "| " + " | ".join([str(v) for v in row.values()]) + " |\n"
        else:
            final_response = f"Error: {db_result['error']}"
        logger.info(f"Tool output: {repr(final_response)}")
        return final_response
    except Exception as e:
        logger.error(f"Error in execute_sql_query: {e}")
        return f"❌ Error: {str(e)}"



sql_execution_tool = Tool(
    name="SQLQueryExecutor",
    func=execute_sql_query,
    description="Generates and executes safe PostgreSQL queries from user questions. Returns both the query explanation and actual database results.",

)


# 5️⃣ Setup Agent with Tool + Memory
def build_agent(llm_instance, memory, callbacks=None):
    return initialize_agent(
        tools=[sql_execution_tool],
        llm=llm_instance,
        agent="zero-shot-react-description",
        verbose=True,
        memory=memory,
        callbacks=callbacks or [],
        handle_parsing_errors=True,
        max_iterations=3
    )

# Use a single memory instance per session (for demo, global)
memory = ConversationBufferMemory(memory_key="chat_history")
agent_executor = build_agent(llm, memory)

db_executor = DatabaseExecutor()

def run_chat_query(user_question, session_id=1):
    try:
        # Load session history into the memory object used by the agent
        load_memory(session_id, memory)
        save_to_chat_db(session_id, 'human', user_question)
        chat_history = get_chat_history_string(session_id)
        user_input = f"Chat history:\n{chat_history}\n\nQuestion:\n{user_question}"
        response = agent_executor.invoke(user_input)
        # Replace default agent iteration/time limit message with custom one
        if isinstance(response, str) and "Agent stopped due to iteration limit or time limit" in response:
            response = "Unable to understand your question."

        sql_query, _ = extract_sql_and_explanation(response)
        save_to_chat_db(session_id, 'assistant', response, sql_query=sql_query, output=response)
        return response
    except Exception as e:
        return f"❌ Error: {str(e)}"

class InventoryChatbot:
    """Main chatbot class for inventory management"""
    
    def __init__(self, user_id: int = None):
        self.user_id = user_id
    
    def create_session(self, user_name: str = "Anonymous") -> ChatSession:
        """Create a new chat session"""
        try:
            # Get or create user (removed last_active_at from defaults)
            user, created = InvitedUser.objects.get_or_create(
                name=user_name
            )
            # Create session
            session = ChatSession.objects.create(
                user=user,
                topic="Inventory Management Query"
            )
            return session
        except Exception as e:
            logger.error(f"Error creating session: {e}")
            raise
    
    def process_question_streaming(self, question: str, session_id: int = None) -> Generator[Dict[str, Any], None, None]:
        """Process a user question and stream both thinking process and final results"""
        
        try:
            # Create session if not provided
            if not session_id:
                session = self.create_session()
                session_id = session.id
            
            # Save user question
            save_to_chat_db(session_id, 'human', question)
            
            # Create a queue for events
            event_queue = queue.Queue()
            final_response = [None]  # Use list to store final response
            

            # Create custom callback that puts events in queue
            class StreamingCallback(BaseCallbackHandler):
                def __init__(self, queue):
                    self.queue = queue
                    self.current_thought = ""
                    self.is_thinking = False
                def on_llm_start(self, serialized, prompts, **kwargs):
                    self.is_thinking = True
                    self.current_thought = ""
                    self.queue.put({"type": "thinking", "content": "\n\n🧠 Starting to think about your question... \n\n"})
                def on_llm_new_token(self, token, **kwargs):
                    if self.is_thinking:
                        self.current_thought += token
                        self.queue.put({"type": "thinking", "content": token})
                def on_llm_end(self, response, **kwargs):
                    self.is_thinking = False
                    if self.current_thought.strip():
                        self.queue.put({"type": "reasoning_step", "content": f"\n\n🧠 **Thought:** {self.current_thought.strip()}\n"})
                def on_chain_start(self, serialized, inputs, **kwargs):
                    chain_name = serialized.get("name", "Unknown") if serialized else "Unknown"
                    if "agent" in chain_name.lower():
                        self.queue.put({"type": "reasoning_step", "content": "🔍 **Analyzing your question...**"})
                def on_agent_action(self, action, **kwargs):
                    if hasattr(action, 'log') and action.log:
                        thought_match = re.search(r'Thought:\s*(.*?)(?:\n|$)', action.log, re.DOTALL)
                        if thought_match:
                            thought = thought_match.group(1).strip()
                            if thought:
                                self.queue.put({"type": "reasoning_step", "content": f"🧠 **Thought:** {thought}"})
                    action_input = action.tool_input if hasattr(action, 'tool_input') else ""
                    self.queue.put({"type": "reasoning_step", "content": f"🔧 **Action:** {action.tool}"})
                    if action_input:
                        self.queue.put({"type": "reasoning_step", "content": f"📥 **Action Input:** {action_input}"})
                def on_tool_start(self, serialized, input_str, **kwargs):
                    tool_name = serialized.get("name", "Unknown Tool") if serialized else "Unknown Tool"
                    self.queue.put({"type": "thinking", "content": f"⚙️ **Executing:** {tool_name}"})
                def on_tool_end(self, output, **kwargs):
                    self.queue.put({"type": "reasoning_step", "content": f"🔍 **Observation:** {str(output)[:200]}{'...' if len(str(output)) > 200 else ''}"})
                def on_chain_end(self, outputs, **kwargs):
                    if outputs and 'output' in outputs:
                        out = outputs['output']
                        # Patch: Replace agent iteration/time limit message with custom one
                        if isinstance(out, str) and out.strip() == "Agent stopped due to iteration limit or time limit.":
                            out = "Unable to understand your question."
                        final_response[0] = out
                        self.queue.put({"type": "final", "content": out})


            # Create callback
            callback = StreamingCallback(event_queue)

            # Function to run agent in background
            def run_agent():
                try:
                    thread_local.event_queue = event_queue
                    streaming_llm = build_llm([callback])
                    # Create a new memory for this streaming session
                    streaming_memory = ConversationBufferMemory(memory_key="chat_history")
                    load_memory(session_id, streaming_memory)
                    chat_history = get_chat_history_string(session_id)
                    user_input = f"Chat history:\n{chat_history}\n\nQuestion:\n{question}"
                    streaming_agent = build_agent(streaming_llm, streaming_memory, [callback])
                    result = streaming_agent.run(user_input)
                    # Patch: Replace agent iteration/time limit message with custom one
                    if isinstance(result, str) and result.strip() == "Agent stopped due to iteration limit or time limit.":
                        result = "Unable to understand your question."
                    if final_response[0] is None:
                        final_response[0] = result
                        event_queue.put({"type": "final", "content": result})
                    save_to_chat_db(session_id, 'assistant', final_response[0])
                    event_queue.put({"type": "end", "content": ""})
                except Exception as e:
                    logger.error(f"Error in agent execution: {e}")
                    event_queue.put({"type": "error", "content": f"Error: {str(e)}"})
                finally:
                    thread_local.event_queue = None

            agent_thread = threading.Thread(target=run_agent)
            agent_thread.daemon = True
            agent_thread.start()

            while True:
                try:
                    event = event_queue.get(timeout=30)
                    if event["type"] == "final":
                        content = event["content"]
                        if content.startswith("✅ **Final Answer:** "):
                            content = content[20:]
                        content = content.replace('\n', '\n')
                        logger.info(f"Final answer content: {repr(content)}")
                        yield {"type": "final", "content": content}
                    elif event["type"] == "end":
                        yield event
                        break
                    elif event["type"] == "error":
                        yield event
                        break
                    else:
                        yield event
                except queue.Empty:
                    if not agent_thread.is_alive():
                        break
                    continue
            
        except Exception as e:
            logger.error(f"Error processing question: {e}")
            error_response = f"I apologize, but I encountered an error while processing your request: {str(e)}"
            yield {"type": "error", "content": error_response}
    

    
    def get_chat_history(self, session_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """Get chat history for a session"""
        try:
            messages = ChatMessage.objects.filter(
                session=session_id
            ).order_by('timestamp')[:limit]
            
            history = []
            for msg in messages:
                history.append({
                    'role': msg.role,
                    'content': msg.content,
                    'timestamp': msg.timestamp.isoformat(),
                    'latency': msg.response_latency,
                    'token_count': msg.token_count,
                    'metadata': msg.metadata
                })
            
            return history
            
        except Exception as e:
            logger.error(f"Error getting chat history: {e}")
            return []
    
    def evaluate_response(self, test_case: str, expected_output: str, actual_output: str) -> Dict[str, Any]:
        """Evaluate a chatbot response"""
        try:
            # Handle expected output as list of keywords or string
            if isinstance(expected_output, list):
                # Check if any keyword is in the actual output
                passed = any(keyword.lower() in actual_output.lower() for keyword in expected_output)
                score = sum(1 for keyword in expected_output if keyword.lower() in actual_output.lower()) / len(expected_output)
            else:
                # Simple string comparison
                passed = expected_output.lower() in actual_output.lower()
                score = 1.0 if passed else 0.0
            
            # Save evaluation
            try:
                evaluation = ChatEvaluation.objects.create(
                    test_case=test_case,
                    expected_output=str(expected_output),
                    actual_output=actual_output,
                    passed=passed,
                    score=score
                )
                
                return {
                    'passed': passed,
                    'score': score,
                    'evaluation_id': evaluation.id
                }
            except Exception as db_error:
                logger.error(f"Error saving evaluation to database: {db_error}")
                return {
                    'passed': passed,
                    'score': score,
                    'evaluation_id': None,
                    'db_error': str(db_error)
                }
            
        except Exception as e:
            logger.error(f"Error evaluating response: {e}")
            return {'error': str(e)}

# 🔁 Example
if __name__ == "__main__":
    question = "List all pending installations for floor 2"
    result = run_chat_query(question, session_id=1)
    print("\n=== Final Answer ===\n")
    print(result)
