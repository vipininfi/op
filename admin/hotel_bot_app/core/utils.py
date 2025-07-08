import logging
from hotel_bot_app.models import ChatSession, ChatMessage
from langchain_core.messages import HumanMessage, AIMessage

logger = logging.getLogger(__name__)


def save_to_chat_db(session_id, role, message, sql_query=None, output=None):
    """Save a chat message to the database."""
    try:
        session = ChatSession.objects.get(id=session_id)
        ChatMessage.objects.create(
            session=session,
            role=role,
            content=message,
            metadata={
                'sql_query': sql_query,
                'output': output
            } if sql_query or output else None
        )
    except Exception as e:
        logger.error(f"[save_to_chat_db] DB Save Error: {e}")


def get_session_history(session_id):
    """Fetch the chat history for a given session."""
    try:
        return ChatMessage.objects.filter(
            session=session_id
        ).order_by('timestamp')
    except Exception as e:
        logger.error(f"[get_session_history] DB Fetch Error: {e}")
        return []


def load_memory(session_id, memory):
    """
    Load session history into LangChain's ConversationBufferMemory
    using proper HumanMessage and AIMessage objects.
    """
    try:
        logger.info(f"[load_memory] Loading session history for session_id={session_id}")
        messages = list(get_session_history(session_id))
        memory.chat_memory.clear()

        # Use the last user/assistant Q&A pair as context for follow-ups
        if not messages:
            return
        # Find the last user message
        last_human_idx = None
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].role == "human":
                last_human_idx = i
                break
        if last_human_idx is None:
            return
        # Optionally, also include the previous Q&A pair if it exists
        # Find the previous user message (before the last one)
        prev_human_idx = None
        for i in range(last_human_idx - 1, -1, -1):
            if messages[i].role == "human":
                prev_human_idx = i
                break
        # Add the previous Q&A pair if it exists
        if prev_human_idx is not None:
            prev_human = messages[prev_human_idx]
            memory.chat_memory.add_message(HumanMessage(content=prev_human.content.strip()))
            if prev_human_idx + 1 < len(messages) and messages[prev_human_idx + 1].role == "assistant":
                prev_ai = messages[prev_human_idx + 1]
                memory.chat_memory.add_message(AIMessage(content=prev_ai.content.strip()))
        # Add the last user message
        last_human = messages[last_human_idx]
        memory.chat_memory.add_message(HumanMessage(content=last_human.content.strip()))
        # If the next message is an assistant message, add it too
        if last_human_idx + 1 < len(messages) and messages[last_human_idx + 1].role == "assistant":
            last_ai = messages[last_human_idx + 1]
            memory.chat_memory.add_message(AIMessage(content=last_ai.content.strip()))
        logger.info(f"[load_memory] Memory loaded with up to 2 user/assistant Q&A pairs (if available).")
    except Exception as e:
        logger.error(f"[load_memory] Error loading memory: {e}")
